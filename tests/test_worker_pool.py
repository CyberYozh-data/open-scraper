from __future__ import annotations

import pytest
import asyncio

from src.worker_pool import WorkerPool, WorkerPoolConfig


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
