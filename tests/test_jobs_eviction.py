from __future__ import annotations

import pytest

from src.jobs import InMemoryJobQueue
from src.schemas import ScrapeRequest


def _page(url: str = "https://example.com") -> ScrapeRequest:
    return ScrapeRequest(url=url)


class TestFinishedAtStamp:
    @pytest.mark.asyncio
    async def test_finished_at_is_none_until_terminal(self):
        queue = InMemoryJobQueue()
        clock = [1000.0]
        queue.set_clock(lambda: clock[0])

        job_id = await queue.submit([_page()])
        assert (await queue.get(job_id)).finished_at is None  # queued

        await queue._set(job_id, status="running")
        assert (await queue.get(job_id)).finished_at is None  # running

    @pytest.mark.asyncio
    async def test_finished_at_stamped_on_terminal(self):
        queue = InMemoryJobQueue()
        clock = [1000.0]
        queue.set_clock(lambda: clock[0])

        job_id = await queue.submit([_page()])
        clock[0] = 1234.0
        await queue._set(job_id, status="done")

        assert (await queue.get(job_id)).finished_at == 1234.0

    @pytest.mark.asyncio
    async def test_finished_at_stamped_on_cancelled_status(self):
        # A queued job cancelled before pickup goes straight to terminal
        # "cancelled" — it must be stamped and become evictable.
        queue = InMemoryJobQueue()
        clock = [500.0]
        queue.set_clock(lambda: clock[0])

        job_id = await queue.submit([_page()])
        await queue._set(job_id, status="cancelled")

        assert (await queue.get(job_id)).finished_at == 500.0

    @pytest.mark.asyncio
    async def test_request_cancel_does_not_stamp_finished_at(self):
        # request_cancel only flags cancelled=True; the job is still in-flight
        # until the worker finalizes it, so it must NOT be evictable yet.
        queue = InMemoryJobQueue()
        queue.set_clock(lambda: 777.0)

        job_id = await queue.submit([_page()])
        assert await queue.request_cancel(job_id) is True

        job_record = await queue.get(job_id)
        assert job_record.cancelled is True
        assert job_record.finished_at is None


class TestSweepExpired:
    @pytest.mark.asyncio
    async def test_sweep_removes_only_old_terminal_jobs(self):
        queue = InMemoryJobQueue(result_ttl_s=100)
        clock = [0.0]
        queue.set_clock(lambda: clock[0])

        old = await queue.submit([_page("https://a.com")])
        await queue._set(old, status="done")  # finished_at = 0

        running = await queue.submit([_page("https://b.com")])
        await queue._set(running, status="running")  # never terminal

        clock[0] = 50.0
        fresh = await queue.submit([_page("https://c.com")])
        await queue._set(fresh, status="done")  # finished_at = 50

        clock[0] = 120.0  # > old TTL (0+100), < fresh TTL (50+100)
        evicted = await queue.sweep_expired()

        assert old in evicted
        assert await queue.get(old) is None          # expired terminal removed
        assert await queue.get(running) is not None  # in-flight never removed
        assert await queue.get(fresh) is not None     # within TTL kept

    @pytest.mark.asyncio
    async def test_sweep_disabled_when_ttl_zero(self):
        queue = InMemoryJobQueue(result_ttl_s=0)
        clock = [0.0]
        queue.set_clock(lambda: clock[0])

        job_id = await queue.submit([_page()])
        await queue._set(job_id, status="done")

        clock[0] = 10_000.0
        assert await queue.sweep_expired() == []
        assert await queue.get(job_id) is not None


class TestCapacityCap:
    @pytest.mark.asyncio
    async def test_submit_evicts_oldest_terminal_over_capacity(self):
        queue = InMemoryJobQueue(max_jobs=2)
        clock = [0.0]
        queue.set_clock(lambda: clock[0])

        first_job_id = await queue.submit([_page("https://a.com")])
        await queue._set(first_job_id, status="done")
        clock[0] = 1.0
        second_job_id = await queue.submit([_page("https://b.com")])
        await queue._set(second_job_id, status="done")

        clock[0] = 2.0
        third_job_id = await queue.submit([_page("https://c.com")])  # 3rd over cap=2

        assert await queue.get(first_job_id) is None          # oldest terminal evicted
        assert await queue.get(second_job_id) is not None
        assert await queue.get(third_job_id) is not None

    @pytest.mark.asyncio
    async def test_capacity_never_evicts_in_flight_jobs(self):
        queue = InMemoryJobQueue(max_jobs=1)

        first_job_id = await queue.submit([_page("https://a.com")])  # queued, non-terminal
        second_job_id = await queue.submit([_page("https://b.com")])  # over cap, but both in-flight

        assert await queue.get(first_job_id) is not None
        assert await queue.get(second_job_id) is not None

    @pytest.mark.asyncio
    async def test_capacity_cap_disabled_when_zero(self):
        queue = InMemoryJobQueue(max_jobs=0)
        clock = [0.0]
        queue.set_clock(lambda: clock[0])

        ids = []
        for index in range(5):
            clock[0] = float(index)
            job_id = await queue.submit([_page(f"https://{index}.com")])
            await queue._set(job_id, status="done")
            ids.append(job_id)

        # No cap → every finished record retained.
        for job_id in ids:
            assert await queue.get(job_id) is not None
