from __future__ import annotations

import fakeredis.aioredis
import pytest
import pytest_asyncio

import src.queue.store as store_mod
from src.queue import reaper
from src.queue.store import RedisJobStore
from src.settings import settings

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(monkeypatch):
    client = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(store_mod, "_store", RedisJobStore(client))
    yield client
    await client.aclose()


async def test_reclaim_redelivers_stale_pending(store, monkeypatch):
    monkeypatch.setattr(settings, "reclaim_idle_s", 0.0)
    await store.xgroup_create("scraper:tasks", "scraper-workers", id="0", mkstream=True)
    await store.xadd("scraper:tasks", {"data": "x"})
    await store.xreadgroup("scraper-workers", "dead-worker", {"scraper:tasks": ">"}, count=1)
    reclaimed = await reaper.reclaim_once()
    assert reclaimed == 1
    fresh = await store.xreadgroup("scraper-workers", "alive", {"scraper:tasks": ">"}, count=10)
    assert fresh and fresh[0][1][0][1] == {b"data": b"x"}


async def test_reclaim_noop_when_nothing_pending(store):
    await store.xgroup_create("scraper:tasks", "scraper-workers", id="0", mkstream=True)
    assert await reaper.reclaim_once() == 0


async def test_reclaim_sweeps_multiple_stale_entries(store, monkeypatch):
    # The loop must keep advancing the cursor until it wraps (0-0), not stop at
    # the first batch — otherwise stale entries past the first scan are missed.
    monkeypatch.setattr(settings, "reclaim_idle_s", 0.0)
    await store.xgroup_create("scraper:tasks", "scraper-workers", id="0", mkstream=True)
    for i in range(5):
        await store.xadd("scraper:tasks", {"data": str(i)})
    await store.xreadgroup("scraper-workers", "dead-worker", {"scraper:tasks": ">"}, count=10)
    reclaimed = await reaper.reclaim_once()
    assert reclaimed == 5  # all stale entries redelivered, none skipped


async def test_reclaim_stale_task_invokes_reclaim_once(mocker):
    """The scheduled task delegates to reclaim_once (the taskiq scheduler fires
    it; a worker runs this)."""
    from unittest.mock import AsyncMock

    from src.queue import tasks as tasks_mod

    spy = mocker.patch.object(tasks_mod, "reclaim_once", new=AsyncMock(return_value=2))
    await tasks_mod.reclaim_stale_task()
    spy.assert_awaited_once()


async def test_reclaim_stale_task_scheduled_every_60s():
    """The reaper runs via a 60s interval schedule label (not a hand-rolled
    loop), so the taskiq scheduler picks it up."""
    from src.queue.tasks import reclaim_stale_task

    schedule = reclaim_stale_task.labels.get("schedule")
    assert schedule and schedule[0]["interval"] == 60
