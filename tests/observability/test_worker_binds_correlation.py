"""The correlation has to be SEEDED, or the whole mechanism is decoration.

A contextvar nobody sets stamps nothing. This drives the real task and asserts
on records the real logger produced.
"""
from __future__ import annotations

import logging

import fakeredis.aioredis
import pytest
import pytest_asyncio

import src.queue.store as store_mod
import src.queue.tasks as tasks
from src.observability.log_context import install_record_factory
from src.queue.broker import broker
from src.queue.store import RedisJobStore
from src.schemas import ScrapeRequest

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _broker():
    await broker.startup()
    yield
    await broker.shutdown()


@pytest_asyncio.fixture
async def store(monkeypatch):
    """Mirrors tests/queue/test_tasks.py: fakeredis plus a stubbed runner, so
    the real task body runs without a browser."""
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


@pytest.fixture(autouse=True)
def _factory():
    install_record_factory()
    yield
    logging.setLogRecordFactory(logging.LogRecord)


async def test_a_failing_page_task_reports_the_id_it_logged(store, monkeypatch, caplog):
    """The whole point, end to end: the id in the caller's response is the id
    the worker logged, on the branch where it used to be a different string."""
    monkeypatch.setattr(tasks.scrape_runner, "run_scrape", _boom())
    job_id = await store.create([ScrapeRequest(url="https://e.com")])
    with caplog.at_level(logging.DEBUG):
        await tasks.scrape_page_task(job_id, 0)

    snap = await store.get_full(job_id)
    returned = snap.results[0].request_id
    assert returned

    logged = {
        getattr(r, "request_id", None) for r in caplog.records
        if getattr(r, "request_id", None)
    }
    assert returned in logged, (
        f"the caller got {returned!r} but the worker logged {logged!r}"
    )


async def test_every_record_from_the_task_carries_the_job(store, monkeypatch, caplog):
    monkeypatch.setattr(tasks.scrape_runner, "run_scrape", _boom())
    job_id = await store.create([ScrapeRequest(url="https://e.com")])
    with caplog.at_level(logging.DEBUG):
        await tasks.scrape_page_task(job_id, 0)

    from_task = [r for r in caplog.records if r.name.startswith("src.queue.tasks")]
    assert from_task, "the task logged nothing at all"
    assert all(getattr(r, "job_id", None) == job_id for r in from_task)
    assert all(getattr(r, "page_index", None) == 0 for r in from_task)


async def test_the_binding_does_not_outlive_the_task(store, monkeypatch, caplog):
    """Bound outside a task — in a worker-startup hook, say — every LATER task
    would inherit it, and the correlation would start lying."""
    monkeypatch.setattr(tasks.scrape_runner, "run_scrape", _boom())
    job_id = await store.create([ScrapeRequest(url="https://e.com")])
    await tasks.scrape_page_task(job_id, 0)

    from src.observability.log_context import current_log_context

    assert current_log_context() == {}


def _boom():
    from unittest.mock import AsyncMock

    return AsyncMock(side_effect=RuntimeError("browser exploded"))
