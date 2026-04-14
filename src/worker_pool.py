from __future__ import annotations

import asyncio
import logging
import queue as pyqueue
import threading
from dataclasses import dataclass
from multiprocessing import get_context, Queue
from typing import Any


log = logging.getLogger(__name__)

# HTTP status codes that indicate a proxy-related failure
_PROXY_BAN_CODES = {401, 403, 407, 429}
_PROXY_TRANSIENT_CODES = {500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526}
_PROXY_SUSPECT_CODES = {413}


@dataclass
class WorkerPoolConfig:
    workers: int
    queue_maxsize: int
    job_timeout_ms: int


class WorkerPool:
    """
    In-memory queue + process workers.
    API enqueues jobs and awaits results via asyncio Future.
    """

    config: WorkerPoolConfig
    task_q: Queue
    result_q: Queue

    _processes: list
    _pending: dict[str, asyncio.Future]
    _pending_lock: threading.Lock
    _result_thread: threading.Thread | None
    _stop_evt: threading.Event

    def init(self, config: WorkerPoolConfig):
        spawn_context = get_context("spawn")
        self.task_q = spawn_context.Queue(maxsize=config.queue_maxsize)
        self.result_q = spawn_context.Queue()
        self.config = config

        self._processes = []
        self._pending = {}
        self._pending_lock = threading.Lock()

        self._result_thread = None
        self._stop_evt = threading.Event()

    def start(self) -> None:
        for worker_index in range(self.config.workers):
            process = get_context("spawn").Process(
                target=_worker_main,
                args=(self.task_q, self.result_q, worker_index),
                daemon=True,
            )
            process.start()
            self._processes.append(process)

        self._result_thread = threading.Thread(target=self._result_pump, daemon=True)
        self._result_thread.start()

    def stop(self) -> None:
        self._stop_evt.set()

        for _ in self._processes:
            try:
                self.task_q.put_nowait({"type": "STOP"})
            except Exception:
                pass

        for process in self._processes:
            try:
                process.join(timeout=2)
            except Exception:
                pass

    async def submit(self, job: dict[str, Any], *, job_timeout_ms: int | None = None) -> dict[str, Any]:
        job_id = job["job_id"]
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        with self._pending_lock:
            self._pending[job_id] = future

        try:
            self.task_q.put(job, block=True, timeout=1.0)
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(job_id, None)
            raise RuntimeError(f"Queue is full / cannot enqueue job: {e}") from e

        timeout = (job_timeout_ms or self.config.job_timeout_ms) / 1000.0
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            with self._pending_lock:
                self._pending.pop(job_id, None)
            raise

    def _result_pump(self) -> None:
        while not self._stop_evt.is_set():
            try:
                result_msg = self.result_q.get(timeout=0.5)
            except pyqueue.Empty:
                continue
            except Exception:
                continue

            job_id = result_msg.get("job_id")
            if not job_id:
                continue

            with self._pending_lock:
                future = self._pending.pop(job_id, None)

            if future is None:
                continue

            try:
                loop = future.get_loop()
                loop.call_soon_threadsafe(future.set_result, result_msg)
            except Exception:
                pass


def _worker_main(task_q, result_q, worker_id: int) -> None:
    """
    Separate process entry point.
    """
    import asyncio  # pylint: disable=redefined-outer-name
    import time
    import traceback

    from src.browser.runner import PlaywrightRunner, FetchResult
    from src.extract.extractor import extract_fields
    from src.extract.models import ExtractRule
    from src.proxy.base import ProxyFailure
    from src.proxy.resolver import proxy_resolver
    from src.settings import settings, setup_logging

    setup_logging(settings.log_level, tag=str(worker_id))

    def looks_like_proxy_failure(status_code: int | None, error: str | None) -> bool:
        if status_code is not None and (
            status_code in _PROXY_BAN_CODES
            or status_code in _PROXY_TRANSIENT_CODES
            or status_code in _PROXY_SUSPECT_CODES
        ):
            return True
        if not error:
            return False
        error = error.lower()
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
        return any(x in error for x in needles)

    async def run() -> None:
        runner = PlaywrightRunner(
            headless=settings.headless,
            block_assets=settings.block_assets,
            timeout_ms=settings.request_timeout_ms,
        )

        try:
            while True:
                job = task_q.get()
                if isinstance(job, dict) and job.get("type") == "STOP":
                    break

                request_id = job["job_id"]
                request: dict[str, Any] = job["request"]

                start_time = time.perf_counter()

                proxy_type_raw = request.get("proxy_type")
                proxy_pool_id = request.get("proxy_pool_id")
                proxy_geo_data = request.get("proxy_geo")
                url = str(request.get("url"))

                proxy_geo: dict[str, str] | None = None
                if proxy_geo_data:
                    proxy_geo = {
                        "country_code": proxy_geo_data.get("country_code"),
                        "region": proxy_geo_data.get("region"),
                        "city": proxy_geo_data.get("city"),
                    }
                    proxy_geo = {k: v for k, v in proxy_geo.items() if v is not None}
                    if not proxy_geo:
                        proxy_geo = None

                log.info(
                    "job received request_id=%s proxy_type=%s proxy_pool_id=%s url=%s",
                    request_id,
                    proxy_type_raw,
                    proxy_pool_id,
                    url,
                )

                try:
                    session = await proxy_resolver.open_session(
                        proxy_type=proxy_type_raw,
                        proxy_pool_id=proxy_pool_id,
                        proxy_geo=proxy_geo
                    )

                    attempts = session.max_attempts()
                    fetch_result: FetchResult | None = None
                    retries_used = 0

                    for attempt in range(1, attempts + 1):
                        proxy_cfg = session.current_proxy()
                        log.debug(
                            "attempt %d/%d for request_id=%s with proxy=%s",
                            attempt,
                            attempts,
                            request_id,
                            proxy_cfg.server if proxy_cfg else "none"
                        )
                        timeout_ms = request.get("timeout_ms") or settings.request_timeout_ms
                        log.debug(
                            "fetching with timeout_ms=%d, wait_until=%s",
                            timeout_ms,
                            request.get("wait_until", "domcontentloaded")
                        )

                        fetch_result = await runner.fetch(
                            url=request["url"],
                            device=request.get("device", "desktop"),
                            proxy=proxy_cfg,
                            headers=request.get("headers"),
                            wait_until=request.get("wait_until", "domcontentloaded"),
                            wait_for_selector=request.get("wait_for_selector"),
                            timeout_ms=request.get("timeout_ms"),
                            screenshot=request.get("screenshot", False),
                        )

                        # If it is success - exit from cycle
                        if fetch_result.ok:
                            log.info(
                                "fetch succeeded on attempt %d for request_id=%s",
                                attempt,
                                request_id
                            )
                            break

                        # If is not success - check, should rotate proxy
                        log.warning(
                            "fetch failed on attempt %d for request_id=%s: %s",
                            attempt,
                            request_id,
                            fetch_result.error
                        )

                        # Check, this error look like proxy problem
                        if not looks_like_proxy_failure(fetch_result.status_code, fetch_result.error):
                            log.info(
                                "error doesn't look like proxy failure, stopping retries for request_id=%s",
                                request_id
                            )
                            break

                        # Increment retry counter
                        retries_used = attempt

                        # Call on_failure for proxy rotation
                        should_retry = await session.on_failure(
                            ProxyFailure(
                                status_code=fetch_result.status_code,
                                error=fetch_result.error
                            )
                        )

                        if not should_retry:
                            log.warning(
                                "session.on_failure returned False, stopping retries for request_id=%s",
                                request_id
                            )
                            break

                        log.info(
                            "rotating to next proxy for request_id=%s, attempt %d/%d",
                            request_id,
                            attempt + 1,
                            attempts
                        )

                    # --- Render result ---
                    warnings: list[str] = []
                    data = None

                    if fetch_result is None:
                        warnings.append("fetch_result_is_none")
                        fetch_result = FetchResult(
                            html="",
                            final_url=None,
                            status_code=None,
                            screenshot_b64=None,
                            ok=False,
                            error="No fetch result"
                        )

                    if not fetch_result.ok:
                        if fetch_result.error:
                            warnings.append(fetch_result.error)
                        if fetch_result.status_code:
                            warnings.append(f"status_code={fetch_result.status_code}")

                    # Extract data if need
                    if request.get("extract") and fetch_result.html:
                        rule = ExtractRule.model_validate(request["extract"])
                        extracted, extraction_warnings = extract_fields(fetch_result.html, rule)
                        data = extracted
                        warnings.extend([str(w) for w in extraction_warnings])

                    raw_html = fetch_result.html if request.get("raw_html", False) else None
                    screenshot_b64 = fetch_result.screenshot_b64 if request.get("screenshot") else None

                    took_ms = int((time.perf_counter() - start_time) * 1000)

                    # Send result
                    result_q.put(
                        {
                            "job_id": request_id,
                            "ok": True,
                            "result": {
                                "request_id": request_id,
                                "took_ms": took_ms,
                                "meta": {
                                    "url": url,
                                    "final_url": fetch_result.final_url,
                                    "status_code": fetch_result.status_code,
                                    "device": request.get("device", "desktop"),
                                    "proxy_type": proxy_type_raw,
                                    "proxy_pool_id": proxy_pool_id,
                                    "retries": retries_used,
                                },
                                "data": data,
                                "raw_html": raw_html,
                                "screenshot_base64": screenshot_b64,
                                "warnings": warnings,
                            },
                        }
                    )

                except Exception as e:
                    error_traceback = traceback.format_exc(limit=20)
                    log.error(
                        "unexpected error in worker for request_id=%s: %s\n%s",
                        request_id,
                        str(e),
                        error_traceback,
                    )
                    result_q.put(
                        {
                            "job_id": request_id,
                            "ok": False,
                            "error": str(e),
                            "traceback": error_traceback,
                        }
                    )
        finally:
            await runner.stop()

    asyncio.run(run())


worker_pool = WorkerPool()
