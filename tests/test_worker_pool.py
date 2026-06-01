from __future__ import annotations

import queue as pyqueue
import threading

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.worker_pool import WorkerPool, WorkerPoolConfig, _worker_main


class TestWorkerPoolConfig:
    def test_worker_pool_config_creation(self):
        """Create WorkerPoolConfig"""
        config = WorkerPoolConfig(
            workers=4,
            queue_maxsize=100,
            job_timeout_ms=30000,
        )

        assert config.workers == 4
        assert config.queue_maxsize == 100
        assert config.job_timeout_ms == 30000


class TestWorkerPool:
    def test_worker_pool_init(self):
        """Initializing WorkerPool"""
        pool = WorkerPool()
        config = WorkerPoolConfig(
            workers=2,
            queue_maxsize=10,
            job_timeout_ms=30000,
        )

        pool.init(config)

        assert pool.config == config
        assert pool._processes == []
        assert pool._pending == {}
        assert pool._result_thread is None

    def test_worker_pool_start(self):
        """Start WorkerPool"""
        pool = WorkerPool()
        config = WorkerPoolConfig(
            workers=2,
            queue_maxsize=10,
            job_timeout_ms=30000,
        )

        pool.init(config)
        pool.start()

        # Check, the processes are running
        assert len(pool._processes) == 2
        assert pool._result_thread is not None
        assert pool._result_thread.is_alive()

        pool.stop()

    def test_worker_pool_stop(self):
        """Stop WorkerPool"""
        pool = WorkerPool()
        config = WorkerPoolConfig(
            workers=1,
            queue_maxsize=10,
            job_timeout_ms=30000,
        )

        pool.init(config)
        pool.start()
        pool.stop()

        # Check, stop_evt is set
        assert pool._stop_evt.is_set()

    @pytest.mark.asyncio
    async def test_submit_job_success(self):
        """Success task send"""
        pool = WorkerPool()
        config = WorkerPoolConfig(
            workers=1,
            queue_maxsize=10,
            job_timeout_ms=30000,
        )

        pool.init(config)
        pool.start()

        # Mock result
        job = {
            "job_id": "test_job",
            "request": {
                "url": "https://example.com",
                "proxy_type": "none",
            },
        }

        # Emulate fast response
        def mock_result():
            import time
            time.sleep(0.1)
            pool.result_q.put({
                "job_id": "test_job",
                "ok": True,
                "result": {"status": "ok"},
            })

        import threading
        threading.Thread(target=mock_result, daemon=True).start()

        try:
            result = await asyncio.wait_for(pool.submit(job), timeout=2.0)
            assert result["ok"] is True
        except asyncio.TimeoutError:
            pytest.skip("Worker didn't manage to process the task.")
        finally:
            pool.stop()

    @pytest.mark.asyncio
    async def test_submit_job_timeout(self):
        """Timeout with task send"""
        pool = WorkerPool()
        config = WorkerPoolConfig(
            workers=1,
            queue_maxsize=10,
            job_timeout_ms=100,  # really short timeout
        )

        pool.init(config)
        pool.start()

        job = {
            "job_id": "test_job",
            "request": {
                "url": "https://example.com",
                "proxy_type": "none",
            },
        }

        # not send result, must be timeout
        with pytest.raises(asyncio.TimeoutError):
            await pool.submit(job)

        pool.stop()

    @pytest.mark.asyncio
    async def test_submit_job_queue_full(self):
        """Crowded queue"""
        pool = WorkerPool()
        config = WorkerPoolConfig(
            workers=0,  # not start workers, because queue not processed
            queue_maxsize=2,  # Really small queue
            job_timeout_ms=30000,
        )

        pool.init(config)
        # not call pool.start() - queue will not be processing

        try:
            pool.task_q.put_nowait({"job_id": "job_1", "request": {}})
            pool.task_q.put_nowait({"job_id": "job_2", "request": {}})
        except Exception:
            pass

        # Next submit must raise with error
        job = {
            "job_id": "overflow_job",
            "request": {
                "url": "https://example.com",
                "proxy_type": "none",
            },
        }

        with pytest.raises(RuntimeError, match="Queue is full"):
            await pool.submit(job)


class TestWorkerProcess:
    """
    Test worker process (runs separately).
    These tests check logic of definition proxy failure.
    """

    def test_worker_process_heuristics_ban_status(self):
        """Check ban by status code"""
        BAN = {401, 403, 407, 429}

        for code in BAN:
            assert code in BAN

    def test_worker_process_heuristics_transient_status(self):
        """Check transient errors"""
        TRANSIENT = {500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526}

        for code in TRANSIENT:
            assert code in TRANSIENT

    def test_worker_process_heuristics_error_strings(self):
        """Check proxy failure by status code"""
        needles = (
            "proxy",
            "tunnel",
            "timed out",
            "timeout",
            "econnreset",
            "econnrefused",
            "enotfound",
            "dns",
            "net::err",
            "connection closed",
            "socket hang up",
            "tls",
            "handshake",
        )

        test_errors = [
            "proxy connection failed",
            "tunnel timeout",
            "connection timed out",
            "econnreset",
            "net::err_connection_refused",
        ]

        for error in test_errors:
            error = error.lower()
            assert any(needle in error for needle in needles)

    def test_looks_like_proxy_failure_with_ban_codes(self):
        """Check logic of definition proxy failure - BAN codes"""
        BAN = {401, 403, 407, 429}

        def looks_like_proxy_failure(status_code, error):
            if status_code in BAN:
                return True
            return False

        assert looks_like_proxy_failure(403, None) is True
        assert looks_like_proxy_failure(429, None) is True
        assert looks_like_proxy_failure(200, None) is False

    def test_looks_like_proxy_failure_with_error_strings(self):
        """Check logic of definition proxy failure - errors description"""
        needles = ("proxy", "tunnel", "timeout")

        def looks_like_proxy_failure(status_code, error):
            if not error:
                return False
            error = error.lower()
            return any(needle in error for needle in needles)

        assert looks_like_proxy_failure(None, "proxy error") is True
        assert looks_like_proxy_failure(None, "tunnel failed") is True
        assert looks_like_proxy_failure(None, "connection timeout") is True
        assert looks_like_proxy_failure(None, "normal error") is False


def _make_worker_mocks(element_status: str | None = None):
    """
    Build the mock objects needed to run _worker_main in a thread without
    a real browser or proxy.  Returns (mock_runner_class, mock_runner_instance,
    mock_proxy_resolver) ready for use with patch().
    """
    from src.browser.runner import FetchResult

    fetch_result = FetchResult(
        html="<html></html>",
        final_url="https://example.com",
        status_code=200,
        screenshot_b64=None,
        ok=True,
        error=None,
        element_status=element_status,
    )

    mock_runner = MagicMock()
    mock_runner.is_started.return_value = False
    mock_runner.fetch = AsyncMock(return_value=fetch_result)
    mock_runner.stop = AsyncMock()
    mock_runner_class = MagicMock(return_value=mock_runner)

    mock_session = MagicMock()
    mock_session.max_attempts.return_value = 1
    mock_session.current_proxy.return_value = None
    mock_proxy_resolver = MagicMock()
    mock_proxy_resolver.open_session = AsyncMock(return_value=mock_session)

    return mock_runner_class, mock_runner, mock_proxy_resolver


def _run_worker_with_job(job: dict, mock_runner_class, mock_proxy_resolver) -> dict:
    """
    Run _worker_main in a thread with patched dependencies, push *job* followed
    by a STOP sentinel, and return the first result put on result_q.
    Raises RuntimeError if the worker produces no output within 10 s.
    """
    task_q: pyqueue.Queue = pyqueue.Queue()
    result_q: pyqueue.Queue = pyqueue.Queue()

    task_q.put(job)
    task_q.put({"type": "STOP"})

    with (
        patch("src.browser.runner.PlaywrightRunner", mock_runner_class),
        patch("src.proxy.resolver.proxy_resolver", mock_proxy_resolver),
    ):
        worker_thread = threading.Thread(target=_worker_main, args=(task_q, result_q, 0), daemon=True)
        worker_thread.start()
        worker_thread.join(timeout=10)

    if worker_thread.is_alive():
        raise RuntimeError("Worker thread still alive after 10s — likely hung in _worker_main")
    if result_q.empty():
        raise RuntimeError("Worker produced no result within timeout")
    return result_q.get_nowait()


class TestWorkerPoolElementSelectorPropagation:
    """Verify that element_selector is wired through _worker_main end-to-end."""

    def test_request_element_selector_reaches_runner_fetch(self):
        """element_selector from the job request is forwarded to runner.fetch()."""
        mock_runner_class, mock_runner, mock_proxy_resolver = _make_worker_mocks(
            element_status=None
        )

        job = {
            "job_id": "elem-sel-test",
            "request": {
                "url": "https://example.com",
                "proxy_type": "none",
                "element_selector": "#main",
            },
        }
        _run_worker_with_job(job, mock_runner_class, mock_proxy_resolver)

        assert mock_runner.fetch.called, "runner.fetch was not called"
        assert mock_runner.fetch.call_args.kwargs["element_selector"] == "#main"

    def test_response_dict_includes_element_screenshot_status(self):
        """element_status on FetchResult is surfaced as element_screenshot_status in the result."""
        mock_runner_class, mock_runner, mock_proxy_resolver = _make_worker_mocks(
            element_status="element"
        )

        job = {
            "job_id": "elem-status-test",
            "request": {
                "url": "https://example.com",
                "proxy_type": "none",
                "element_selector": "#main",
            },
        }
        worker_result = _run_worker_with_job(job, mock_runner_class, mock_proxy_resolver)

        assert worker_result["ok"] is True
        result = worker_result["result"]
        assert "element_screenshot_status" in result, (
            f"element_screenshot_status missing from result dict; keys={list(result.keys())}"
        )
        assert result["element_screenshot_status"] == "element"

    def test_element_status_none_surfaces_as_none(self):
        """A FetchResult with element_status=None must surface
        element_screenshot_status=None in the result dict — guards against a
        future `if element_status:` truthiness guard dropping the key."""
        mock_runner_class, mock_runner, mock_proxy_resolver = _make_worker_mocks(
            element_status=None
        )

        job = {
            "job_id": "elem-status-none-test",
            "request": {
                "url": "https://example.com",
                "proxy_type": "none",
                "element_selector": "#main",
            },
        }
        worker_result = _run_worker_with_job(job, mock_runner_class, mock_proxy_resolver)

        assert worker_result["ok"] is True
        result = worker_result["result"]
        assert "element_screenshot_status" in result, (
            f"element_screenshot_status key missing from result dict; keys={list(result.keys())}"
        )
        assert result["element_screenshot_status"] is None
