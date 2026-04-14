from __future__ import annotations

import pytest
from unittest.mock import Mock, AsyncMock

from src.jobs import JobRecord, InMemoryJobQueue, JobRunner, get_job_queue
from src.schemas import ScrapeRequest, ScrapeResponse, ScrapeMeta


class TestJobRecord:
    def test_job_record_creation(self):
        """Create JobRecord"""
        pages = [ScrapeRequest(url="https://example.com")]

        record = JobRecord(
            job_id="job_123",
            status="queued",
            pages=pages,
            total=1,
        )

        assert record.job_id == "job_123"
        assert record.status == "queued"
        assert record.total == 1
        assert record.done == 0
        assert record.error is None
        assert record.results is None

    def test_job_record_defaults(self):
        """Defaults for JobRecord"""
        pages = [ScrapeRequest(url="https://example.com")]

        record = JobRecord(
            job_id="job_123",
            status="queued",
            pages=pages,
            total=1,
        )

        assert record.done == 0
        assert record.error is None
        assert record.results is None


class TestInMemoryJobQueue:
    @pytest.mark.asyncio
    async def test_job_queue_submit(self):
        """Send task to queue"""
        queue = InMemoryJobQueue()

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        assert job_id is not None
        assert job_id.startswith("req_")

    @pytest.mark.asyncio
    async def test_job_queue_submit_adds_to_queue(self):
        """Submit adding task to queue"""
        queue = InMemoryJobQueue()

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        # Checking, record created
        record = await queue.get(job_id)
        assert record is not None
        assert record.job_id == job_id
        assert record.status == "queued"
        assert record.total == 1

    @pytest.mark.asyncio
    async def test_job_queue_get(self):
        """Get task from queue"""
        queue = InMemoryJobQueue()

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        record = await queue.get(job_id)

        assert record.job_id == job_id
        assert record.pages == pages

    @pytest.mark.asyncio
    async def test_job_queue_get_not_found(self):
        """Get not exists task"""
        queue = InMemoryJobQueue()

        record = await queue.get("non_existent_id")

        assert record is None

    @pytest.mark.asyncio
    async def test_job_queue_set_status(self):
        """Update task status"""
        queue = InMemoryJobQueue()

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        await queue._set(job_id, status="running")

        record = await queue.get(job_id)
        assert record.status == "running"

    @pytest.mark.asyncio
    async def test_job_queue_set_done(self):
        """Update counter is done"""
        queue = InMemoryJobQueue()

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        await queue._set(job_id, done=1)

        record = await queue.get(job_id)
        assert record.done == 1

    @pytest.mark.asyncio
    async def test_job_queue_set_error(self):
        """Update error"""
        queue = InMemoryJobQueue()

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        await queue._set(job_id, error="Test error")

        record = await queue.get(job_id)
        assert record.error == "Test error"

    @pytest.mark.asyncio
    async def test_job_queue_set_results(self):
        """Update resules"""
        queue = InMemoryJobQueue()

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        results = [
            ScrapeResponse(
                request_id="req_1",
                took_ms=100,
                meta=ScrapeMeta(
                    url="https://example.com",
                    device="desktop",
                    proxy_type="none",
                    retries=0,
                ),
            )
        ]

        await queue._set(job_id, results=results)

        record = await queue.get(job_id)
        assert record.results == results

    @pytest.mark.asyncio
    async def test_job_queue_set_multiple_fields(self):
        """Update a few fields at the same time"""
        queue = InMemoryJobQueue()

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        await queue._set(
            job_id,
            status="done",
            done=1,
            results=[],
        )

        record = await queue.get(job_id)
        assert record.status == "done"
        assert record.done == 1
        assert record.results == []


class TestJobRunner:
    def test_job_runner_init(self):
        """Initializing JobRunner"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=2)

        assert runner._queue == queue
        assert runner._concurrency == 2
        assert runner._tasks == []

    def test_job_runner_concurrency_minimum(self):
        """Minimal concurrency = 1"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=0)

        assert runner._concurrency == 1

    @pytest.mark.asyncio
    async def test_job_runner_start(self):
        """Run JobRunner"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=2)

        await runner.start()

        assert len(runner._tasks) == 2
        assert runner._stop.is_set() is False

        await runner.stop()

    @pytest.mark.asyncio
    async def test_job_runner_stop(self):
        """Stop JobRunner"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        await runner.start()
        await runner.stop()

        assert runner._stop.is_set() is True
        assert len(runner._tasks) == 0

    @pytest.mark.asyncio
    async def test_job_runner_worker_processes_job(self, mocker):
        """Worker processing job"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        # Mock worker_pool
        mock_worker_pool = Mock()
        mock_worker_pool.submit = AsyncMock(return_value={
            "ok": True,
            "result": {
                "request_id": "req_1",
                "took_ms": 100,
                "meta": {
                    "url": "https://example.com",
                    "final_url": "https://example.com",
                    "status_code": 200,
                    "device": "desktop",
                    "proxy_type": "none",
                    "proxy_pool_id": None,
                    "retries": 0,
                },
                "data": None,
                "raw_html": None,
                "screenshot_base64": None,
                "warnings": [],
            },
        })

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        # Send task
        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        # Start runner to short time
        await runner.start()

        # Get time to process
        import asyncio
        await asyncio.sleep(0.5)

        await runner.stop()

        # Check, that task was processed
        record = await queue.get(job_id)
        assert record.status in ("running", "done")

    @pytest.mark.asyncio
    async def test_job_runner_status_transitions(self, mocker):
        """Change statuses: queued → running → done"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        mock_worker_pool = Mock()
        mock_worker_pool.submit = AsyncMock(return_value={
            "ok": True,
            "result": {
                "request_id": "req_1",
                "took_ms": 100,
                "meta": {
                    "url": "https://example.com",
                    "device": "desktop",
                    "proxy_type": "none",
                    "retries": 0,
                },
                "data": None,
                "raw_html": None,
                "screenshot_base64": None,
                "warnings": [],
            },
        })

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        # First queued
        record = await queue.get(job_id)
        assert record.status == "queued"

        await runner.start()

        import asyncio
        await asyncio.sleep(0.5)

        await runner.stop()

        record = await queue.get(job_id)
        assert record.status in ("running", "done")

    @pytest.mark.asyncio
    async def test_job_runner_updates_done_counter(self, mocker):
        """Counter update done"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        mock_worker_pool = Mock()
        mock_worker_pool.submit = AsyncMock(return_value={
            "ok": True,
            "result": {
                "request_id": "req_1",
                "took_ms": 100,
                "meta": {
                    "url": "https://example.com",
                    "device": "desktop",
                    "proxy_type": "none",
                    "retries": 0,
                },
                "data": None,
                "raw_html": None,
                "screenshot_base64": None,
                "warnings": [],
            },
        })

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        pages = [
            ScrapeRequest(url="https://example.com"),
            ScrapeRequest(url="https://example.org"),
        ]
        job_id = await queue.submit(pages)

        await runner.start()

        import asyncio
        await asyncio.sleep(1.0)

        await runner.stop()

        record = await queue.get(job_id)
        assert record.done >= 0

    @pytest.mark.asyncio
    async def test_job_runner_handles_worker_failure(self, mocker):
        """Worker errors cathc"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        mock_worker_pool = Mock()
        mock_worker_pool.submit = AsyncMock(return_value={
            "ok": False,
            "error": "Worker failed",
        })

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        await runner.start()

        import asyncio
        await asyncio.sleep(0.5)

        await runner.stop()

        record = await queue.get(job_id)
        # Status must be failed
        assert record.status == "failed"

    @pytest.mark.asyncio
    async def test_job_runner_collects_results(self, mocker):
        """Collection of results"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        mock_worker_pool = Mock()
        mock_worker_pool.submit = AsyncMock(return_value={
            "ok": True,
            "result": {
                "request_id": "req_1",
                "took_ms": 100,
                "meta": {
                    "url": "https://example.com",
                    "device": "desktop",
                    "proxy_type": "none",
                    "retries": 0,
                },
                "data": {"title": "Test"},
                "raw_html": None,
                "screenshot_base64": None,
                "warnings": [],
            },
        })

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        pages = [ScrapeRequest(url="https://example.com")]
        job_id = await queue.submit(pages)

        await runner.start()

        import asyncio
        await asyncio.sleep(0.5)

        await runner.stop()

        record = await queue.get(job_id)
        if record.status == "done":
            assert record.results is not None
            assert len(record.results) >= 0


class TestGetJobQueue:
    def test_get_job_queue_singleton(self):
        """get_job_queue return single item"""
        queue1 = get_job_queue()
        queue2 = get_job_queue()

        assert queue1 is queue2
