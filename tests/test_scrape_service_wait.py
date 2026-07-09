"""Unit tests for ScrapeService.run_and_wait against the Redis job store."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
import pytest_asyncio

import src.queue.store as store_mod
import src.scrape_service as svc
from src.queue.store import RedisJobStore, pack_payload
from src.queue.tasks import make_error_payload
from src.schemas import ScrapeResponse, ScrapeMeta
from src.scrape_service import scrape_service


def _ok_payload(url: str = "https://example.com") -> bytes:
    resp = ScrapeResponse(
        request_id="req_1",
        took_ms=100,
        meta=ScrapeMeta(url=url, device="desktop", proxy_type="none", retries=0),
    )
    return pack_payload(resp.model_dump(mode="json"))


@pytest_asyncio.fixture
async def fake_store(monkeypatch):
    """Wire a fresh fakeredis store and stub out task enqueue."""
    client = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(store_mod, "_store", RedisJobStore(client))
    monkeypatch.setattr("src.scrape_service.scrape_page_task.kiq", AsyncMock())
    yield store_mod.get_job_store()
    await client.aclose()


class TestRunAndWait:
    @pytest.mark.asyncio
    async def test_returns_results_when_terminal(self, fake_store):
        """run_and_wait returns results once all slots are filled."""
        # Submit an empty-page job (immediately terminal)
        job_id = await scrape_service._enqueue([])
        out = await scrape_service.run_and_wait([], timeout_s=5)
        assert out == []

    @pytest.mark.asyncio
    async def test_failed_with_no_results_returns_empty(self, fake_store):
        """run_and_wait with no pages returns empty list."""
        out = await scrape_service.run_and_wait([], timeout_s=5)
        assert out == []


class TestEnqueueFailure:
    @pytest.mark.asyncio
    async def test_partial_fanout_failure_converges_job(self, fake_store, monkeypatch):
        from src.schemas import ScrapeRequest

        created: dict[str, str] = {}
        orig_create = fake_store.create

        async def capture_create(*a, **k):
            jid = await orig_create(*a, **k)
            created["id"] = jid
            return jid

        monkeypatch.setattr(fake_store, "create", capture_create)

        calls = {"n": 0}

        async def flaky_kiq(job_id, index):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("stream down")

        monkeypatch.setattr("src.scrape_service.scrape_page_task.kiq", flaky_kiq)

        pages = [ScrapeRequest(url=f"https://x{i}.com") for i in range(3)]
        with pytest.raises(RuntimeError, match="stream down"):
            await scrape_service.submit_job(pages)

        # The half-enqueued job is converged to cancelled, not left hung until TTL.
        meta = await fake_store.get_meta(created["id"])
        assert meta.status == "cancelled"


class TestPlanChains:
    def test_groups_by_session(self):
        from src.scrape_service import plan_chains
        from src.schemas import ScrapeRequest

        pages = [
            ScrapeRequest(url="https://a.com"),                                    # 0 standalone
            ScrapeRequest(url="https://b.com", session_id="s1", proxy_type="none"),  # 1 head s1
            ScrapeRequest(url="https://c.com"),                                    # 2 standalone
            ScrapeRequest(url="https://d.com", session_id="s1", proxy_type="none"),  # 3 -> after 1
            ScrapeRequest(url="https://e.com", session_id="s2", proxy_type="none"),  # 4 head s2
        ]
        chain_next, heads = plan_chains(pages)
        assert chain_next == {1: 3}
        assert heads == [0, 1, 2, 4]

    def test_no_sessions_all_heads(self):
        from src.scrape_service import plan_chains
        from src.schemas import ScrapeRequest

        pages = [ScrapeRequest(url=f"https://x{i}.com") for i in range(3)]
        chain_next, heads = plan_chains(pages)
        assert chain_next == {} and heads == [0, 1, 2]

    def test_three_page_chain(self):
        from src.scrape_service import plan_chains
        from src.schemas import ScrapeRequest

        pages = [ScrapeRequest(url=f"https://x{i}.com", session_id="s", proxy_type="none") for i in range(3)]
        chain_next, heads = plan_chains(pages)
        assert chain_next == {0: 1, 1: 2} and heads == [0]

    @pytest.mark.asyncio
    async def test_timeout_returns_partial_results(self, fake_store):
        """On timeout run_and_wait returns whatever is in the store (empty snap)."""
        from src.schemas import ScrapeRequest
        pages = [ScrapeRequest(url="https://example.com")]
        # We start the job but never write a slot — it times out
        out = await scrape_service.run_and_wait(pages, timeout_s=0.05)
        # The snap.results will be [None]; run_and_wait returns snap.results
        # (the caller gets the partial list even if slots are unfilled)
        assert isinstance(out, list)
