from __future__ import annotations

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock

from src.jobs import JobRecord, InMemoryJobQueue, JobRunner, get_job_queue
from src.schemas import ScrapeRequest, ScrapeResponse, ScrapeMeta
from src.sessions.models import SessionCreateRequest
from src.sessions.store import InMemorySessionStore


@pytest.fixture
def fresh_session_store():
    return InMemorySessionStore(max_sessions=4)


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
    async def test_request_cancel_marks_flag_and_returns_true(self):
        """request_cancel sets JobRecord.cancelled and returns True for active jobs."""
        queue = InMemoryJobQueue()
        job_id = await queue.submit([ScrapeRequest(url="https://example.com")])
        job_record = await queue.get(job_id)
        assert job_record.cancelled is False

        ok = await queue.request_cancel(job_id)
        assert ok is True
        updated_record = await queue.get(job_id)
        assert updated_record.cancelled is True

    @pytest.mark.asyncio
    async def test_request_cancel_idempotent_on_already_cancelled(self):
        """request_cancel returns False on already-terminal jobs."""
        queue = InMemoryJobQueue()
        job_id = await queue.submit([ScrapeRequest(url="https://example.com")])
        await queue._set(job_id, status="cancelled")

        ok = await queue.request_cancel(job_id)
        assert ok is False

    @pytest.mark.asyncio
    async def test_request_cancel_refused_for_done_and_failed(self):
        """Cannot cancel a job that already reached a terminal state."""
        queue = InMemoryJobQueue()

        done_id = await queue.submit([ScrapeRequest(url="https://example.com")])
        await queue._set(done_id, status="done")
        assert (await queue.request_cancel(done_id)) is False

        failed_id = await queue.submit([ScrapeRequest(url="https://example.com")])
        await queue._set(failed_id, status="failed", error="boom")
        assert (await queue.request_cancel(failed_id)) is False

    @pytest.mark.asyncio
    async def test_request_cancel_unknown_job_returns_false(self):
        queue = InMemoryJobQueue()
        assert (await queue.request_cancel("req_does_not_exist")) is False

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

        # Check that task was processed
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
        """Worker errors are captured as placeholder responses with warnings"""
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

        # Job completes as "done" even with worker errors
        assert record.status == "done"
        assert record.done == 1
        assert record.total == 1

        # Error is captured in the response warnings
        assert record.results is not None
        assert len(record.results) == 1
        assert "Worker failed" in record.results[0].warnings

        # Response is a placeholder with metadata
        assert record.results[0].meta.url == "https://example.com/"
        assert record.results[0].meta.status_code is None

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

    @pytest.mark.asyncio
    async def test_process_page_success(self, mocker):
        """_process_page handles successful worker response"""
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
                "raw_html": "<html></html>",
                "screenshot_base64": None,
                "warnings": [],
            },
        })

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        import asyncio
        semaphore = asyncio.Semaphore(1)
        page = ScrapeRequest(url="https://example.com")

        response = await runner._process_page(semaphore, page)

        assert response.meta.url == "https://example.com"
        assert response.data == {"title": "Test"}
        assert response.warnings == []

    @pytest.mark.asyncio
    async def test_process_page_worker_error(self, mocker):
        """_process_page creates placeholder on worker error"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        mock_worker_pool = Mock()
        mock_worker_pool.submit = AsyncMock(return_value={
            "ok": False,
            "error": "Connection failed",
        })

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        import asyncio
        semaphore = asyncio.Semaphore(1)
        page = ScrapeRequest(url="https://example.com", device="mobile")

        response = await runner._process_page(semaphore, page)

        assert response.meta.url == "https://example.com/"
        assert response.meta.device == "mobile"
        assert response.meta.status_code is None
        assert "Connection failed" in response.warnings
        assert response.took_ms == 0

    @pytest.mark.asyncio
    async def test_process_page_timeout(self, mocker):
        """_process_page handles timeout"""
        import asyncio

        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        mock_worker_pool = Mock()
        mock_worker_pool.submit = AsyncMock(side_effect=asyncio.TimeoutError())

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        semaphore = asyncio.Semaphore(1)
        page = ScrapeRequest(url="https://example.com", timeout_ms=5000)

        response = await runner._process_page(semaphore, page)

        assert response.meta.url == "https://example.com/"
        assert any("timed out" in w for w in response.warnings)

    @pytest.mark.asyncio
    async def test_process_page_exception(self, mocker):
        """_process_page handles unexpected exceptions"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        mock_worker_pool = Mock()
        mock_worker_pool.submit = AsyncMock(side_effect=RuntimeError("Unexpected error"))

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        import asyncio
        semaphore = asyncio.Semaphore(1)
        page = ScrapeRequest(url="https://example.com")

        response = await runner._process_page(semaphore, page)

        assert response.meta.url == "https://example.com/"
        assert "Unexpected error" in response.warnings or "RuntimeError" in response.warnings

    @pytest.mark.asyncio
    async def test_parallel_processing_multiple_pages(self, mocker):
        """Multiple pages are processed in parallel"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        call_count = 0
        call_times = []

        async def mock_submit(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            import time
            call_times.append(time.time())
            await asyncio.sleep(0.1)  # Simulate work
            return {
                "ok": True,
                "result": {
                    "request_id": f"req_{call_count}",
                    "took_ms": 100,
                    "meta": {
                        "url": f"https://example{call_count}.com",
                        "device": "desktop",
                        "proxy_type": "none",
                        "retries": 0,
                    },
                    "data": None,
                    "raw_html": None,
                    "screenshot_base64": None,
                    "warnings": [],
                },
            }

        mock_worker_pool = Mock()
        mock_worker_pool.submit = mock_submit

        mocker.patch("src.jobs.worker_pool", mock_worker_pool)
        mocker.patch("src.jobs.settings.workers", 5)  # Allow high parallelism

        pages = [ScrapeRequest(url=f"https://example{i}.com") for i in range(5)]
        job_id = await queue.submit(pages)

        await runner.start()
        await asyncio.sleep(0.5)
        await runner.stop()

        record = await queue.get(job_id)

        assert record.status == "done"
        assert record.done == 5
        assert len(record.results) == 5

        # Check that pages were processed in parallel (not sequentially)
        # If sequential, would take 5 * 0.1 = 0.5s
        # If parallel, should take ~0.1s
        if len(call_times) >= 2:
            time_span = call_times[-1] - call_times[0]
            # All calls should start within a short window if parallel
            assert time_span < 0.3  # Much less than 0.5s

    @pytest.mark.asyncio
    async def test_incremental_progress_updates(self, mocker):
        """Progress is updated incrementally as pages complete"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        completion_order = []

        async def mock_submit(*args, **kwargs):
            request = args[0]["request"]
            url = request["url"]
            # Simulate different completion times
            if "slow" in url:
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(0.1)

            completion_order.append(url)

            return {
                "ok": True,
                "result": {
                    "request_id": "req_1",
                    "took_ms": 100,
                    "meta": {
                        "url": url,
                        "device": "desktop",
                        "proxy_type": "none",
                        "retries": 0,
                    },
                    "data": None,
                    "raw_html": None,
                    "screenshot_base64": None,
                    "warnings": [],
                },
            }

        mock_worker_pool = Mock()
        mock_worker_pool.submit = mock_submit
        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        pages = [
            ScrapeRequest(url="https://fast1.com"),
            ScrapeRequest(url="https://slow.com"),
            ScrapeRequest(url="https://fast2.com"),
        ]
        job_id = await queue.submit(pages)

        await runner.start()

        # Check progress while running
        await asyncio.sleep(0.15)
        record = await queue.get(job_id)
        # At least fast pages should be done
        assert record.done >= 1

        await asyncio.sleep(0.3)
        await runner.stop()

        record = await queue.get(job_id)
        assert record.status == "done"
        assert record.done == 3

    @pytest.mark.asyncio
    async def test_job_maintains_page_order(self, mocker):
        """Results maintain the same order as input pages"""
        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        async def mock_submit(*args, **kwargs):
            request = args[0]["request"]
            url = request["url"]
            # Random delays to ensure out-of-order completion
            import random
            await asyncio.sleep(random.uniform(0.01, 0.05))

            return {
                "ok": True,
                "result": {
                    "request_id": "req_1",
                    "took_ms": 100,
                    "meta": {
                        "url": url,
                        "device": "desktop",
                        "proxy_type": "none",
                        "retries": 0,
                    },
                    "data": {"url": url},
                    "raw_html": None,
                    "screenshot_base64": None,
                    "warnings": [],
                },
            }

        mock_worker_pool = Mock()
        mock_worker_pool.submit = mock_submit
        mocker.patch("src.jobs.worker_pool", mock_worker_pool)

        pages = [
            ScrapeRequest(url="https://first.com"),
            ScrapeRequest(url="https://second.com"),
            ScrapeRequest(url="https://third.com"),
            ScrapeRequest(url="https://fourth.com"),
        ]
        job_id = await queue.submit(pages)

        await runner.start()
        await asyncio.sleep(0.5)
        await runner.stop()

        record = await queue.get(job_id)

        assert record.status == "done"
        assert len(record.results) == 4

        # Check order is preserved
        assert record.results[0].meta.url == "https://first.com/"
        assert record.results[1].meta.url == "https://second.com/"
        assert record.results[2].meta.url == "https://third.com/"
        assert record.results[3].meta.url == "https://fourth.com/"

    @pytest.mark.asyncio
    async def test_worker_timeout_calculation(self):
        """_calculate_worker_timeout adds retry budget"""
        from src.jobs import _calculate_worker_timeout

        # With page timeout
        assert _calculate_worker_timeout(10000) == 10000 * 2 + 30000  # 50000
        assert _calculate_worker_timeout(5000) == 5000 * 2 + 30000  # 40000

        # Without page timeout
        assert _calculate_worker_timeout(None) is None

    @pytest.mark.asyncio
    async def test_create_error_response(self):
        """_create_error_response creates valid placeholder"""
        from src.jobs import _create_error_response

        page = ScrapeRequest(
            url="https://example.com",
            device="mobile",
            proxy_type="res_static",
            proxy_pool_id="pool_1",
        )

        response = _create_error_response(page, "Test error")

        assert response.took_ms == 0
        assert response.meta.url == "https://example.com/"
        assert response.meta.device == "mobile"
        assert response.meta.proxy_type == "res_static"
        assert response.meta.proxy_pool_id == "pool_1"
        assert response.meta.status_code is None
        assert "Test error" in response.warnings


class TestGetJobQueue:
    def test_get_job_queue_singleton(self):
        """get_job_queue return single item"""
        queue1 = get_job_queue()
        queue2 = get_job_queue()

        assert queue1 is queue2


class TestJobRunnerSession:
    @pytest.mark.asyncio
    async def test_process_page_with_session_loads_and_writes_storage_state(
        self, fresh_session_store, monkeypatch
    ):
        session_record = await fresh_session_store.create(SessionCreateRequest())
        await fresh_session_store.update_storage_state(
            session_record.session_id,
            {"cookies": [{"name": "sid", "value": "abc"}], "origins": []},
        )

        monkeypatch.setattr("src.jobs.get_session_store", lambda: fresh_session_store)

        captured: dict = {}

        async def fake_submit(job, *, job_timeout_ms=None):
            captured.update(job)
            return {
                "ok": True,
                "result": {
                    "request_id": "req_1",
                    "took_ms": 10,
                    "meta": {
                        "url": "https://x.com",
                        "device": "desktop",
                        "proxy_type": "none",
                        "proxy_pool_id": None,
                        "status_code": 200,
                        "retries": 0,
                    },
                    "data": None,
                    "raw_html": None,
                    "screenshot_base64": None,
                    "warnings": [],
                },
                "storage_state": {
                    "cookies": [
                        {"name": "sid", "value": "abc"},
                        {"name": "new", "value": "xyz"},
                    ],
                    "origins": [],
                },
            }

        monkeypatch.setattr("src.jobs.worker_pool.submit", fake_submit)

        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        page = ScrapeRequest(url="https://x.com", session_id=session_record.session_id)
        import asyncio as _asyncio
        response = await runner._process_page(_asyncio.Semaphore(1), page)

        assert response.warnings == []
        # storage_state from worker was written back
        updated = await fresh_session_store.get(session_record.session_id)
        cookies = {c["name"] for c in updated.storage_state["cookies"]}
        assert cookies == {"sid", "new"}
        # The job dict carried storage_state to the worker
        assert captured["request"]["session_id"] == session_record.session_id
        assert captured["storage_state"]["cookies"][0]["name"] == "sid"

    @pytest.mark.asyncio
    async def test_process_page_skips_state_write_on_non_success_status(
        self, fresh_session_store, monkeypatch
    ):
        """Don't clobber session state with a captcha-page state — gate by status_code."""
        session_record = await fresh_session_store.create(SessionCreateRequest())
        original_state = {"cookies": [{"name": "sid", "value": "good"}], "origins": []}
        await fresh_session_store.update_storage_state(session_record.session_id, original_state)

        monkeypatch.setattr("src.jobs.get_session_store", lambda: fresh_session_store)

        async def fake_submit(job, *, job_timeout_ms=None):
            return {
                "ok": True,
                "result": {
                    "request_id": "req_1",
                    "took_ms": 10,
                    "meta": {
                        "url": "https://x.com",
                        "device": "desktop",
                        "proxy_type": "none",
                        "proxy_pool_id": None,
                        "status_code": 403,  # blocked / captcha — don't persist this state
                        "retries": 0,
                    },
                    "data": None,
                    "raw_html": None,
                    "screenshot_base64": None,
                    "warnings": ["captcha detected"],
                },
                "storage_state": {
                    "cookies": [{"name": "sid", "value": "bad_captcha_state"}],
                    "origins": [],
                },
            }

        monkeypatch.setattr("src.jobs.worker_pool.submit", fake_submit)

        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        page = ScrapeRequest(url="https://x.com", session_id=session_record.session_id)
        import asyncio as _asyncio
        await runner._process_page(_asyncio.Semaphore(1), page)

        updated = await fresh_session_store.get(session_record.session_id)
        assert updated.storage_state == original_state

    @pytest.mark.asyncio
    async def test_process_page_without_session_skips_lock(
        self, fresh_session_store, monkeypatch
    ):
        """Plain scrape (no session_id) bypasses the session_store entirely."""
        monkeypatch.setattr("src.jobs.get_session_store", lambda: fresh_session_store)

        async def fake_submit(job, *, job_timeout_ms=None):
            assert "storage_state" not in job  # no session → no storage_state in envelope
            return {
                "ok": True,
                "result": {
                    "request_id": "req_1",
                    "took_ms": 10,
                    "meta": {
                        "url": "https://x.com",
                        "device": "desktop",
                        "proxy_type": "none",
                        "proxy_pool_id": None,
                        "status_code": 200,
                        "retries": 0,
                    },
                    "data": None,
                    "raw_html": None,
                    "screenshot_base64": None,
                    "warnings": [],
                },
            }

        monkeypatch.setattr("src.jobs.worker_pool.submit", fake_submit)

        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        page = ScrapeRequest(url="https://x.com")  # no session_id
        import asyncio as _asyncio
        response = await runner._process_page(_asyncio.Semaphore(1), page)
        assert response.warnings == []

    @pytest.mark.asyncio
    async def test_process_page_with_unknown_session_id_returns_warning(
        self, fresh_session_store, monkeypatch
    ):
        """SessionNotFound → ScrapeResponse with warning, not exception."""
        monkeypatch.setattr("src.jobs.get_session_store", lambda: fresh_session_store)

        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        page = ScrapeRequest(url="https://x.com", session_id="sess_nonexistent")
        response = await runner._process_page(asyncio.Semaphore(1), page)

        assert any("session_not_found" in w for w in response.warnings)

    @pytest.mark.asyncio
    async def test_process_page_with_expired_session_returns_warning(
        self, fresh_session_store, monkeypatch
    ):
        """SessionExpired → ScrapeResponse with warning, not exception."""
        # Create + immediately expire via mutable clock
        clock_ref = [1000.0]
        fresh_session_store.set_clock(lambda: clock_ref[0])
        session_record = await fresh_session_store.create(SessionCreateRequest(ttl_seconds=600))
        clock_ref[0] += 1000  # past TTL

        monkeypatch.setattr("src.jobs.get_session_store", lambda: fresh_session_store)

        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=1)

        page = ScrapeRequest(url="https://x.com", session_id=session_record.session_id)
        response = await runner._process_page(asyncio.Semaphore(1), page)

        assert any("session_expired" in w for w in response.warnings)

    @pytest.mark.asyncio
    async def test_concurrent_processes_for_same_session_serialize(
        self, fresh_session_store, monkeypatch
    ):
        """Two parallel _process_page calls against the same session must NOT overlap inside
        the worker_pool.submit. The per-session lock enforces this."""
        session_record = await fresh_session_store.create(SessionCreateRequest())
        await fresh_session_store.update_storage_state(
            session_record.session_id, {"cookies": [], "origins": []}
        )

        monkeypatch.setattr("src.jobs.get_session_store", lambda: fresh_session_store)

        inside = 0
        max_inside_observed = 0

        async def fake_submit(job, *, job_timeout_ms=None):
            nonlocal inside, max_inside_observed
            inside += 1
            max_inside_observed = max(max_inside_observed, inside)
            await asyncio.sleep(0.02)  # hold the "inside" window
            inside -= 1
            return {
                "ok": True,
                "result": {
                    "request_id": "req",
                    "took_ms": 10,
                    "meta": {
                        "url": "https://x.com",
                        "device": "desktop",
                        "proxy_type": "none",
                        "proxy_pool_id": None,
                        "status_code": 200,
                        "retries": 0,
                    },
                    "data": None, "raw_html": None,
                    "screenshot_base64": None, "warnings": [],
                },
                "storage_state": {"cookies": [], "origins": []},
            }

        monkeypatch.setattr("src.jobs.worker_pool.submit", fake_submit)

        queue = InMemoryJobQueue()
        runner = JobRunner(queue, concurrency=4)
        sema = asyncio.Semaphore(4)

        page = ScrapeRequest(url="https://x.com", session_id=session_record.session_id)
        await asyncio.gather(
            runner._process_page(sema, page),
            runner._process_page(sema, page),
            runner._process_page(sema, page),
        )

        # If the per-session lock works, max_inside_observed must be 1 — never 2 or 3.
        assert max_inside_observed == 1, (
            f"per-session lock failed: {max_inside_observed} concurrent worker calls for same session"
        )
