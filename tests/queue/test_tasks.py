from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio

import src.queue.store as store_mod
from src.proxy.base import ProxyConfigError
from src.queue import tasks
from src.queue.broker import broker
from src.queue.store import RedisJobStore, pack_payload, unpack_payload
from src.schemas import ScrapeRequest, ScrapeResponse

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def broker_lifecycle():
    """InMemoryBroker must be started before tasks can be called."""
    await broker.startup()
    yield
    await broker.shutdown()


@pytest_asyncio.fixture
async def store(monkeypatch):
    from src.sessions.store import init_session_store
    client = fakeredis.aioredis.FakeRedis()
    s = RedisJobStore(client, result_ttl_s=600.0)
    monkeypatch.setattr(store_mod, "_store", s)
    async def _fake_get_runner(*a, **k):
        return object()
    monkeypatch.setattr(tasks, "_get_runner", _fake_get_runner)
    init_session_store(client)
    yield s
    await client.aclose()


def _ok_envelope(req_id="req_x"):
    return {"ok": True, "storage_state": None, "result": {
        "request_id": req_id, "took_ms": 1,
        "meta": {"url": "https://e.com", "device": "desktop", "proxy_type": "none",
                 "status_code": 200, "retries": 0},
        "warnings": [],
    }}


@pytest.fixture
def scrape_ok(monkeypatch):
    mock = AsyncMock(return_value=_ok_envelope())
    monkeypatch.setattr(tasks.scrape_runner, "run_scrape", mock)
    return mock


async def test_task_writes_slot_and_finalizes(store, scrape_ok, monkeypatch):
    kiq = AsyncMock()
    monkeypatch.setattr(tasks.scrape_page_task, "kiq", kiq)
    job_id = await store.create([ScrapeRequest(url="https://e.com")])
    await tasks.scrape_page_task(job_id, 0)
    meta = await store.get_meta(job_id)
    assert meta.status == "done" and meta.done == 1
    scrape_ok.assert_awaited_once()
    kiq.assert_not_awaited()  # standalone page has no chain_next


async def test_task_failure_becomes_error_slot(store, monkeypatch):
    monkeypatch.setattr(tasks.scrape_runner, "run_scrape",
                        AsyncMock(side_effect=RuntimeError("browser exploded")))
    job_id = await store.create([ScrapeRequest(url="https://e.com")])
    await tasks.scrape_page_task(job_id, 0)
    snap = await store.get_full(job_id)
    assert snap.status == "done"
    assert "browser exploded" in (snap.results[0].warnings or [])
    # The reason must also be a first-class result field — clients need
    # something structured to display, not only a warnings entry.
    assert snap.results[0].error == "browser exploded"
    # Hard worker failure must report fetch_ok=False, not inherit the True default.
    assert snap.results[0].meta.fetch_ok is False


async def test_login_config_error_classified_without_traceback(monkeypatch):
    # A session pin with unsatisfiable proxy targeting is the same user-input
    # class the scrape path reports — no traceback-bearing crash envelope.
    monkeypatch.setattr(
        tasks.proxy_resolver, "open_session",
        AsyncMock(side_effect=ProxyConfigError("no v2 geo suffix for name='Dagestan'")),
    )
    runner = MagicMock()
    runner.start = AsyncMock()
    envelope = await tasks._run_login(runner, {
        "session_pin": {
            "proxy_type": "prem_res_rotating",
            "proxy_geo": {"country_code": "CA", "region": "Dagestan"},
            "device": "desktop",
        },
        "script": {}, "creds": {},
    })
    assert envelope["ok"] is False
    # Same "TypeName: message" format the session errors use.
    assert envelope["error"].startswith("ProxyConfigError:")
    assert "Dagestan" in envelope["error"]
    assert "traceback" not in envelope


async def test_task_noops_on_cancelled_job(store, scrape_ok):
    job_id = await store.create([ScrapeRequest(url="https://e.com")])
    await store.request_cancel(job_id, stub_payloads={0: pack_payload(
        tasks.make_error_payload({"url": "https://e.com"}, "cancelled"))})
    await tasks.scrape_page_task(job_id, 0)
    scrape_ok.assert_not_awaited()


async def test_redelivery_fast_forwards_chain(store, scrape_ok, monkeypatch):
    pages = [ScrapeRequest(url="https://e.com/1"), ScrapeRequest(url="https://e.com/2")]
    job_id = await store.create(pages, chain_next={0: 1})
    await store.write_slot(job_id, 0, pack_payload(_ok_envelope()["result"]))  # slot pre-filled
    mark_running = AsyncMock()
    monkeypatch.setattr(store, "mark_running", mark_running)
    kiq = AsyncMock()
    monkeypatch.setattr(tasks.scrape_page_task, "kiq", kiq)
    await tasks.scrape_page_task(job_id, 0)   # redelivery
    scrape_ok.assert_not_awaited()            # no double scrape
    mark_running.assert_not_awaited()         # redelivery doesn't re-mark
    kiq.assert_awaited_once_with(job_id, 1)   # chain advanced


async def test_missing_payload_still_converges_job(store, scrape_ok):
    # If the page payload is gone (corruption / TTL race), the task must still
    # write a slot so the job finalizes instead of hanging in "running".
    # (get_full can't reconstruct pages here — the hash is gone — so assert
    # convergence via meta + a written slot, which is the invariant that matters.)
    job_id = await store.create([ScrapeRequest(url="https://e.com")])
    await store.client.delete(f"job:{job_id}:pages")  # simulate vanished payload
    await tasks.scrape_page_task(job_id, 0)
    scrape_ok.assert_not_awaited()
    meta = await store.get_meta(job_id)
    assert meta.status == "done" and meta.done == 1  # converged, not stuck in "running"
    assert await store.slot_filled(job_id, 0) is True  # an (error) slot was written
    # the error slot must be a valid ScrapeResponse on read-back (empty page dict
    # must still produce Literal-valid meta fields)
    raw = await store.client.hget(f"job:{job_id}:results", "0")
    ScrapeResponse.model_validate(unpack_payload(raw))  # must not raise


async def test_timeout_becomes_error_slot(store, monkeypatch):
    async def _hang(*a, **k):
        import asyncio as _a
        await _a.sleep(9999)
    monkeypatch.setattr(tasks.scrape_runner, "run_scrape", _hang)
    monkeypatch.setattr(tasks.settings, "page_task_timeout_s", 0.05)
    job_id = await store.create([ScrapeRequest(url="https://e.com")])
    await tasks.scrape_page_task(job_id, 0)
    snap = await store.get_full(job_id)
    assert snap.status == "done"
    assert any("exceeded" in w for w in (snap.results[0].warnings or []))


async def test_missing_job_drops_without_crash(store, scrape_ok):
    await tasks.scrape_page_task("req_nonexistent", 0)  # no such job
    scrape_ok.assert_not_awaited()


async def test_chain_kiq_after_successful_scrape(store, scrape_ok, monkeypatch):
    pages = [ScrapeRequest(url="https://e.com/1"), ScrapeRequest(url="https://e.com/2")]
    job_id = await store.create(pages, chain_next={0: 1})
    kiq = AsyncMock()
    monkeypatch.setattr(tasks.scrape_page_task, "kiq", kiq)
    await tasks.scrape_page_task(job_id, 0)
    kiq.assert_awaited_once_with(job_id, 1)


async def test_login_task_getdel_and_result_key(store, monkeypatch):
    monkeypatch.setattr(tasks, "_run_login",
                        AsyncMock(return_value={"ok": True,
                                                "login_result": {"ok": True, "took_ms": 5},
                                                "storage_state": {"cookies": []}}))
    await store.client.set(tasks.LOGIN_PAYLOAD_KEY.format("lg1"),
                           json.dumps({"session_pin": {}, "script": {}, "creds": {}}))
    await tasks.login_task("lg1")
    raw = await store.client.get(tasks.LOGIN_RESULT_KEY.format("lg1"))
    assert json.loads(raw)["ok"] is True
    assert await store.client.get(tasks.LOGIN_PAYLOAD_KEY.format("lg1")) is None  # consumed


async def test_login_task_missing_payload(store):
    await tasks.login_task("nope")
    raw = await store.client.get(tasks.LOGIN_RESULT_KEY.format("nope"))
    assert json.loads(raw)["ok"] is False


async def test_login_task_exception_writes_error_result(store, monkeypatch):
    monkeypatch.setattr(tasks, "_run_login", AsyncMock(side_effect=RuntimeError("login boom")))
    await store.client.set(tasks.LOGIN_PAYLOAD_KEY.format("lg2"),
                           json.dumps({"session_pin": {}, "script": {}, "creds": {}}))
    await tasks.login_task("lg2")
    res = json.loads(await store.client.get(tasks.LOGIN_RESULT_KEY.format("lg2")))
    assert res["ok"] is False and "login boom" in res["error"]


async def test_session_not_found_becomes_error_slot(store, scrape_ok):
    # A page pinned to a non-existent session: the session lock acquisition
    # raises SessionNotFound, which must convert to an error slot (not crash).
    job_id = await store.create(
        [ScrapeRequest(url="https://e.com", session_id="sess_missing", proxy_type="none")]
    )
    await tasks.scrape_page_task(job_id, 0)
    scrape_ok.assert_not_awaited()  # never reached the scrape
    snap = await store.get_full(job_id)
    assert snap.status == "done"
    assert any("SessionNotFound" in w for w in (snap.results[0].warnings or []))


@pytest.mark.asyncio
async def test_get_runner_no_deadlock_when_caller_holds_browser_lock(monkeypatch):
    """_get_runner must NOT re-acquire browser_lock (asyncio.Lock is non-reentrant).

    Before the fix, _get_runner did `async with state.browser_lock:` while
    _browser_guard already held the same lock, causing an instant deadlock.
    This test reproduces that path: acquire the lock (as _browser_guard does),
    then call _get_runner — it must complete within 2s, not deadlock.
    """
    import asyncio
    import types

    class _StubRunner:
        _engine = "firefox"

        async def start(self):
            pass

    # _get_runner imports _new_runner lazily from src.queue.worker (to avoid a
    # circular import), so patch it on the worker module where it's looked up.
    monkeypatch.setattr("src.queue.worker._new_runner", lambda engine="chromium": _StubRunner())
    state = types.SimpleNamespace(
        runners={},
        last_activity={},
        pages_since_launch={},
        browser_lock=asyncio.Lock(),
    )
    ctx = types.SimpleNamespace(state=state)
    async with tasks._browser_guard(ctx):  # holds browser_lock (as _scrape() does)
        runner = await asyncio.wait_for(
            tasks._get_runner(ctx, "firefox"), timeout=2.0
        )  # must NOT deadlock
    assert runner._engine == "firefox"


async def test_scrape_with_session_injects_pinned_viewport(monkeypatch):
    """A session scrape presents the session's pinned viewport, overriding
    whatever the request carried, so login and every scrape share one size."""
    from src.schemas import Viewport
    from src.sessions.models import SessionRecord

    record = SessionRecord(
        session_id="s1", status="ready", created_at=0, expires_at=1, last_used_at=0,
        device="desktop", proxy_type="none",
        viewport=Viewport(width=1366, height=768), storage_state=None,
    )

    class _Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    store = MagicMock()
    store.lock = MagicMock(return_value=_Lock())
    store.get = AsyncMock(return_value=record)
    monkeypatch.setattr(tasks, "get_session_store", lambda: store)

    captured = {}

    async def fake_run_scrape(runner, request_id, page, storage_state):
        captured["page"] = page
        return {"ok": False}

    monkeypatch.setattr(tasks.scrape_runner, "run_scrape", fake_run_scrape)

    page = {"session_id": "s1", "url": "https://x", "viewport": {"width": 9999, "height": 9999}}
    await tasks._scrape_with_session(MagicMock(), "req1", page)

    assert captured["page"]["viewport"] == {"width": 1366, "height": 768}
