from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict

from src.schemas import JobStatus, ScrapeRequest, ScrapeResponse
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


class InMemoryJobQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

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
        """Process a single page with retry budget and error handling."""
        timeout_ms = _calculate_worker_timeout(page.timeout_ms)

        try:
            async with semaphore:
                worker_result = await worker_pool.submit(
                    {"job_id": new_request_id(), "request": page.model_dump(mode="json")},
                    job_timeout_ms=timeout_ms,
                )

            if worker_result.get("ok", False):
                return ScrapeResponse.model_validate(worker_result["result"])
            else:
                error = worker_result.get("error", "worker_failed")
                return _create_error_response(page, error)

        except asyncio.TimeoutError:
            error = f"worker_pool timed out after {timeout_ms or 'default'}ms"
            return _create_error_response(page, error)
        except Exception as exc:
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
    ) -> None:
        """Process a page and update job progress."""
        response = await self._process_page(semaphore, page)
        results[index] = response

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

            await self._queue._set(job_id, status="running", done=0)

            try:
                semaphore = asyncio.Semaphore(max(1, int(settings.workers)))
                results: list[ScrapeResponse] = [None] * len(job_record.pages)  # type: ignore
                done_counter = [0]
                done_lock = asyncio.Lock()

                await asyncio.gather(
                    *(
                        self._process_and_update(job_id, i, page, semaphore, results, done_counter, done_lock)
                        for i, page in enumerate(job_record.pages)
                    ),
                    return_exceptions=False,
                )

                await self._queue._set(job_id, status="done", done=job_record.total, results=results)

            except Exception as e:
                await self._queue._set(job_id, status="failed", error=str(e) or type(e).__name__)


job_queue = InMemoryJobQueue()


def get_job_queue() -> InMemoryJobQueue:
    return job_queue
