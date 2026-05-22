from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict

from src.schemas import JobStatus, ScrapeRequest, ScrapeResponse
from src.sessions.store import (
    SessionExpired,
    SessionIncompatible,
    SessionNotFound,
    get_session_store,
)
from src.settings import settings
from src.utils.ids import new_request_id
from src.worker_pool import worker_pool

log = logging.getLogger(__name__)


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus
    pages: list[ScrapeRequest]
    total: int
    done: int = 0
    error: str | None = None
    results: list[ScrapeResponse] | None = None
    cancelled: bool = False


def _calculate_worker_timeout(page_timeout_ms: int | None) -> int | None:
    """Calculate worker_pool timeout with budget for retries and proxy rotation."""
    if not page_timeout_ms:
        return None
    return page_timeout_ms * 2 + 30000


def _create_error_response(page: ScrapeRequest, error: str) -> ScrapeResponse:
    """Create a placeholder ScrapeResponse for failed pages."""
    return ScrapeResponse(
        request_id=new_request_id(),
        took_ms=0,
        meta={
            "url": str(page.url),
            "device": page.device,
            "proxy_type": page.proxy_type,
            "proxy_pool_id": page.proxy_pool_id,
            "status_code": None,
            "retries": 0,
        },
        warnings=[error],
    )


def _is_successful_status(status_code: int | None) -> bool:
    """200-399 is the persist gate. 4xx/5xx (incl. captcha 403) and None mean don't write back state."""
    return status_code is not None and 200 <= int(status_code) < 400


class InMemoryJobQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    def clear(self):
        """
        Re-bind asyncio primitives to the current event loop. The job_queue
        singleton is created at module import time (no running loop), so its
        asyncio.Queue and asyncio.Lock are unbound. They bind to the first loop
        that uses them. Resetting here ensures each lifespan (prod: once;
        tests: once per TestClient context) gets fresh primitives on the correct loop.
        """
        self._queue = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._jobs = {}

    async def submit(self, pages: list[ScrapeRequest]) -> str:
        job_id = new_request_id()
        job_record = JobRecord(job_id=job_id, status="queued", pages=pages, total=len(pages))
        async with self._lock:
            self._jobs[job_id] = job_record
        await self._queue.put(job_id)
        return job_id

    async def get(self, job_id: str) -> JobRecord | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def request_cancel(self, job_id: str) -> bool:
        """Mark a job as cancelled. In-flight pages finish; remaining pages
        are skipped and the job transitions to ``status="cancelled"``."""
        async with self._lock:
            job_record = self._jobs.get(job_id)
            if job_record is None or job_record.status in ("done", "failed", "cancelled"):
                log.info("cancel request ignored job_id=%s reason=%s", job_id,
                         "not_found" if job_record is None else f"already_{job_record.status}")
                return False
            job_record.cancelled = True
        log.info("cancel requested job_id=%s status=%s", job_id, job_record.status)
        return True

    async def next_job(self) -> str:
        """Get next job ID from the queue."""
        return await self._queue.get()

    async def _set(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        done: int | None = None,
        error: str | None = None,
        results: list[ScrapeResponse] | None = None,
    ) -> None:
        async with self._lock:
            job_record = self._jobs.get(job_id)
            if job_record is None:
                return
            if status is not None:
                job_record.status = status
            if done is not None:
                job_record.done = done
            if error is not None:
                job_record.error = error
            if results is not None:
                job_record.results = results


class JobRunner:
    def __init__(self, queue: InMemoryJobQueue, *, concurrency: int = 1) -> None:
        self._queue = queue
        self._concurrency = max(1, int(concurrency))
        self._tasks: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._tasks = [asyncio.create_task(self._worker(worker_index)) for worker_index in range(self._concurrency)]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _process_page(self, semaphore: asyncio.Semaphore, page: ScrapeRequest) -> ScrapeResponse:
        """Process a single page with retry budget and error handling.

        When ``page.session_id`` is set, the entire load-submit-store cycle runs
        under the per-session ``asyncio.Lock`` so concurrent scrapes against the
        same session serialize and don't clobber each other's ``storage_state``.
        """
        timeout_ms = _calculate_worker_timeout(page.timeout_ms)
        session_id = page.session_id
        store = get_session_store()

        try:
            if session_id:
                async with store.lock(session_id):
                    session_record = await store.get(session_id)
                    # Defense in depth: API layer 422s session_id + cookies, but if a caller
                    # constructs a ScrapeRequest internally we still don't want both.
                    request_dict = page.model_dump(mode="json")
                    if request_dict.get("cookies"):
                        log.warning(
                            "session_id=%s came with cookies — ignoring cookies to honor session pin",
                            session_id,
                        )
                        request_dict["cookies"] = None
                    job_dict = {
                        "job_id": new_request_id(),
                        "request": request_dict,
                        "storage_state": session_record.storage_state,
                    }
                    async with semaphore:
                        worker_result = await worker_pool.submit(
                            job_dict, job_timeout_ms=timeout_ms,
                        )

                    if worker_result.get("ok", False):
                        new_state = worker_result.get("storage_state")
                        status_code = (
                            worker_result.get("result", {}).get("meta", {}).get("status_code")
                        )
                        if new_state is not None and _is_successful_status(status_code):
                            await store.update_storage_state(session_id, new_state)
                        return ScrapeResponse.model_validate(worker_result["result"])
                    error = worker_result.get("error", "worker_failed")
                    return _create_error_response(page, error)
            else:
                async with semaphore:
                    worker_result = await worker_pool.submit(
                        {"job_id": new_request_id(), "request": page.model_dump(mode="json")},
                        job_timeout_ms=timeout_ms,
                    )

                if worker_result.get("ok", False):
                    return ScrapeResponse.model_validate(worker_result["result"])
                error = worker_result.get("error", "worker_failed")
                return _create_error_response(page, error)

        except SessionNotFound:
            log.warning("session not found: page=%s session_id=%s", page.url, session_id)
            return _create_error_response(page, f"session_not_found: {session_id}")
        except SessionExpired as exc:
            log.warning(
                "session expired: page=%s session_id=%s expired_at=%s",
                page.url, session_id, exc.expired_at,
            )
            return _create_error_response(page, f"session_expired: {exc.session_id}")
        except SessionIncompatible as exc:
            log.warning(
                "session incompatible: page=%s session_id=%s reason=%s",
                page.url, session_id, exc,
            )
            return _create_error_response(page, f"session_incompatible: {exc}")
        except asyncio.TimeoutError:
            error = f"worker_pool timed out after {timeout_ms or 'default'}ms"
            return _create_error_response(page, error)
        except Exception as exc:  # pylint: disable=broad-except
            error = str(exc) or type(exc).__name__
            return _create_error_response(page, error)

    async def _process_and_update(
        self,
        job_id: str,
        index: int,
        page: ScrapeRequest,
        semaphore: asyncio.Semaphore,
        results: list[ScrapeResponse],
        done_counter: list[int],
        done_lock: asyncio.Lock,
        job_record: JobRecord,
    ) -> None:
        """Process a page and update job progress. Skip if job is cancelled."""
        if job_record.cancelled:
            results[index] = _create_error_response(page, "cancelled")
        else:
            results[index] = await self._process_page(semaphore, page)

        async with done_lock:
            done_counter[0] += 1
            await self._queue._set(job_id, done=done_counter[0], results=results)

    async def _worker(self, worker_index: int) -> None:
        log.info("job_runner started worker_index=%s", worker_index)

        while not self._stop.is_set():
            job_id = await self._queue.next_job()
            job_record = await self._queue.get(job_id)
            if job_record is None:
                continue

            if job_record.cancelled:
                await self._queue._set(job_id, status="cancelled", done=0)
                continue

            await self._queue._set(job_id, status="running", done=0)

            try:
                semaphore = asyncio.Semaphore(max(1, int(settings.workers)))
                results: list[ScrapeResponse] = [None] * len(job_record.pages)  # type: ignore
                done_counter = [0]
                done_lock = asyncio.Lock()

                await asyncio.gather(
                    *(
                        self._process_and_update(job_id, i, page, semaphore, results, done_counter, done_lock, job_record)
                        for i, page in enumerate(job_record.pages)
                    ),
                    return_exceptions=False,
                )

                final_status: JobStatus = "cancelled" if job_record.cancelled else "done"
                await self._queue._set(job_id, status=final_status, done=job_record.total, results=results)

            except Exception as e:
                await self._queue._set(job_id, status="failed", error=str(e) or type(e).__name__)


job_queue = InMemoryJobQueue()


def get_job_queue() -> InMemoryJobQueue:
    return job_queue
