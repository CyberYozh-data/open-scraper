from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest
import pytest_asyncio

from src.queue.store import (
    SAFETY_TTL_S,
    JobNotInStore,
    RedisJobStore,
    pack_payload,
    unpack_payload,
)
from src.schemas import ScrapeRequest

pytestmark = pytest.mark.asyncio


def _page(url: str = "https://example.com", **kw) -> ScrapeRequest:
    return ScrapeRequest(url=url, **kw)


def _result_dict(i: int, **overrides) -> dict:
    # Minimal ScrapeResponse-shaped dict; the store treats payloads as opaque.
    base = {
        "request_id": f"req_{i}",
        "took_ms": 10,
        "meta": {"url": "https://example.com", "device": "desktop",
                 "proxy_type": "none", "status_code": 200, "retries": 0},
        "warnings": [],
    }
    base.update(overrides)
    return base


@pytest_asyncio.fixture
async def store():
    client = fakeredis.aioredis.FakeRedis()
    s = RedisJobStore(client, result_ttl_s=600.0)
    yield s
    await client.aclose()


async def test_create_and_get_meta(store):
    job_id = await store.create([_page(), _page()])
    meta = await store.get_meta(job_id)
    assert meta is not None
    assert meta.status == "queued"
    assert meta.total == 2 and meta.done == 0
    assert meta.cancelled is False
    assert 0 < await store.client.ttl(f"job:{job_id}") <= SAFETY_TTL_S


async def test_get_meta_missing_returns_none(store):
    assert await store.get_meta("req_nope") is None


async def test_write_slot_increments_done_once(store):
    job_id = await store.create([_page(), _page()])
    out = await store.write_slot(job_id, 0, pack_payload(_result_dict(0)))
    # Contract: write_slot never touches queued->running (that's mark_running's
    # job, done by the task before scraping); it only counts and finalizes.
    assert out.done == 1 and out.status == "queued" and out.was_new is True


async def test_write_slot_is_idempotent_on_redelivery(store):
    job_id = await store.create([_page(), _page()])
    await store.write_slot(job_id, 0, pack_payload(_result_dict(0)))
    out = await store.write_slot(job_id, 0, pack_payload(_result_dict(0)))  # redelivery
    assert out.done == 1  # no double count
    meta = await store.get_meta(job_id)
    assert meta.done == 1 and meta.status not in ("done", "failed", "cancelled")


async def test_first_slot_arms_safety_ttl_on_results_key(store):
    # The results key doesn't exist at create() time, so it must pick up the
    # 24h orphan guard on its first write — otherwise a job that writes some
    # slots but never finalizes would leak its (heavy) result blobs forever.
    job_id = await store.create([_page(), _page()])
    assert await store.client.ttl(f"job:{job_id}:results") == -2  # key absent yet
    await store.write_slot(job_id, 0, pack_payload(_result_dict(0)))  # non-final write
    ttl = await store.client.ttl(f"job:{job_id}:results")
    assert 600 < ttl <= SAFETY_TTL_S  # safety TTL, not the shorter result TTL


async def test_last_slot_finalizes_done_and_arms_ttl(store):
    job_id = await store.create([_page(), _page()])
    await store.write_slot(job_id, 0, pack_payload(_result_dict(0)))
    out = await store.write_slot(job_id, 1, pack_payload(_result_dict(1)))
    assert out.status == "done" and out.done == 2
    meta = await store.get_meta(job_id)
    assert meta.status == "done" and meta.finished_at is not None
    # all three keys re-armed to the shorter result TTL on finalization
    for suffix in ("", ":results", ":pages"):
        ttl = await store.client.ttl(f"job:{job_id}{suffix}")
        assert 0 < ttl <= 600


async def test_mark_running_only_from_queued(store):
    job_id = await store.create([_page()])
    assert await store.mark_running(job_id) is True
    assert await store.mark_running(job_id) is False  # already running
    meta = await store.get_meta(job_id)
    assert meta.status == "running"


async def test_cancel_fill_stubs_and_finalizes_instantly(store):
    job_id = await store.create([_page(), _page(), _page()])
    await store.mark_running(job_id)
    await store.write_slot(job_id, 0, pack_payload(_result_dict(0)))
    stubs = {1: pack_payload(_result_dict(1)), 2: pack_payload(_result_dict(2))}
    assert await store.request_cancel(job_id, stub_payloads=stubs) is True
    meta = await store.get_meta(job_id)
    assert meta.status == "cancelled" and meta.cancelled is True and meta.done == 3
    assert await store.request_cancel(job_id, stub_payloads={}) is False  # terminal no-op


async def test_late_result_overwrites_stub_without_unfinalizing(store):
    job_id = await store.create([_page(), _page()])
    await store.request_cancel(
        job_id, stub_payloads={0: pack_payload(_result_dict(0)), 1: pack_payload(_result_dict(1))}
    )
    out = await store.write_slot(job_id, 0, pack_payload(_result_dict(0, request_id="late", took_ms=5)))
    assert out.done == 2 and out.status == "cancelled"  # counters untouched, still cancelled
    snap = await store.get_full(job_id)
    assert snap.results[0].request_id == "late"  # value overwritten


async def test_get_full_null_padding_and_order(store):
    job_id = await store.create([_page("https://a.com"), _page("https://b.com"), _page("https://c.com")])
    await store.write_slot(job_id, 1, pack_payload(_result_dict(1)))
    snap = await store.get_full(job_id)
    assert [str(p.url) for p in snap.pages] == ["https://a.com/", "https://b.com/", "https://c.com/"]
    assert snap.results[0] is None and snap.results[2] is None
    assert snap.results[1].request_id == "req_1"


async def test_write_slot_on_missing_job_reports_orphan(store):
    with pytest.raises(JobNotInStore):
        await store.write_slot("req_gone", 0, pack_payload(_result_dict(0)))


async def test_concurrent_writes_count_exactly_once_per_slot(store):
    job_id = await store.create([_page() for _ in range(10)])
    async def hit(i: int):
        await store.write_slot(job_id, i % 10, pack_payload(_result_dict(i % 10)))
    await asyncio.gather(*(hit(i) for i in range(40)))  # 4 deliveries per slot
    meta = await store.get_meta(job_id)
    assert meta.done == 10 and meta.status == "done"


async def test_chain_next_round_trip(store):
    pages = [_page(), _page(session_id="sess_x", proxy_type="none"),
             _page(session_id="sess_x", proxy_type="none")]
    job_id = await store.create(pages, chain_next={1: 2})
    assert await store.get_chain_next(job_id, 1) == 2
    assert await store.get_chain_next(job_id, 0) is None
    assert await store.get_chain_next(job_id, 2) is None


async def test_pack_unpack_round_trip():
    d = _result_dict(7)
    assert unpack_payload(pack_payload(d)) == d


async def test_create_empty_job_is_immediately_done(store):
    job_id = await store.create([])
    meta = await store.get_meta(job_id)
    assert meta.status == "done" and meta.total == 0 and meta.done == 0
    assert meta.finished_at is not None
    snap = await store.get_full(job_id)
    assert snap.pages == [] and snap.results == []


async def test_slot_filled_reflects_writes(store):
    job_id = await store.create([_page(), _page()])
    assert await store.slot_filled(job_id, 0) is False
    await store.write_slot(job_id, 0, pack_payload(_result_dict(0)))
    assert await store.slot_filled(job_id, 0) is True
    assert await store.slot_filled(job_id, 1) is False


async def test_get_page_returns_payload_or_none(store):
    job_id = await store.create([_page("https://only.example")])
    page = await store.get_page(job_id, 0)
    assert page is not None and page["url"] == "https://only.example/"
    assert await store.get_page(job_id, 5) is None


async def test_get_meta_exposes_created_at(store):
    job_id = await store.create([_page()])
    meta = await store.get_meta(job_id)
    assert meta.created_at > 0


# --- queue_depth: live backlog, not lifetime stream length -------------------

_STREAM = "scraper:tasks"
_GROUP = "scraper-workers"


async def _deliver(client, n: int, *, count: int) -> list[bytes]:
    """Add n entries, create the group, deliver `count` of them; return the
    delivered message ids (so the test can ack them)."""
    for i in range(n):
        await client.xadd(_STREAM, {"task": f"t{i}"})
    await client.xgroup_create(_STREAM, _GROUP, id="0")
    msgs = await client.xreadgroup(_GROUP, "c1", {_STREAM: ">"}, count=count)
    return [mid for _stream, entries in msgs for mid, _fields in entries]


async def test_queue_depth_ignores_finished_entries(store):
    """Regression: a stream keeps every entry until trimmed and taskiq acks
    but never deletes, so XLEN counts long-finished jobs. queue_depth must
    report 0 once every entry is read AND acked, even though XLEN is unchanged
    — otherwise backpressure rejects all submits after QUEUE_MAXSIZE lifetime
    jobs."""
    client = store.client
    delivered = await _deliver(client, 5, count=5)
    await client.xack(_STREAM, _GROUP, *delivered)
    assert await client.xlen(_STREAM) == 5      # nothing trimmed
    assert await store.queue_depth(_STREAM) == 0  # but no live backlog


async def test_queue_depth_counts_undelivered_plus_inflight(store):
    """Depth = undelivered (lag) + delivered-but-unacked (pending)."""
    client = store.client
    delivered = await _deliver(client, 4, count=2)  # 2 in-flight, 2 waiting
    assert await store.queue_depth(_STREAM) == 4
    await client.xack(_STREAM, _GROUP, *delivered)  # 2 done, 2 waiting
    assert await store.queue_depth(_STREAM) == 2


async def test_queue_depth_no_group_uses_xlen(store):
    """Cold start: entries enqueued before a worker created the group are all
    undelivered, so depth equals XLEN."""
    client = store.client
    for i in range(3):
        await client.xadd(_STREAM, {"task": f"t{i}"})
    assert await store.queue_depth(_STREAM) == 3


async def test_queue_depth_missing_stream_is_zero(store):
    assert await store.queue_depth(_STREAM) == 0


async def test_queue_depth_response_error_is_zero(store, mocker):
    """Real Redis raises ResponseError (NOGROUP / no such key) where fakeredis
    returns []; the production `except` branch must map that to 0. (A transport
    failure raises ConnectionError, a sibling — not ResponseError — so it still
    propagates rather than masquerading as an empty queue.)"""
    from unittest.mock import AsyncMock

    from redis.exceptions import ResponseError

    mocker.patch.object(
        store.client, "xinfo_groups",
        new=AsyncMock(side_effect=ResponseError("no such key")),
    )
    assert await store.queue_depth(_STREAM) == 0


async def test_queue_depth_none_lag_counts_only_pending(store, mocker):
    """Redis reports lag=None when entries were trimmed out from under the
    group; we treat undelivered as drained (safe direction) but must still
    count in-flight `pending`."""
    from unittest.mock import AsyncMock

    mocker.patch.object(
        store.client, "xinfo_groups",
        new=AsyncMock(return_value=[{"name": _GROUP, "lag": None, "pending": 2}]),
    )
    assert await store.queue_depth(_STREAM) == 2
