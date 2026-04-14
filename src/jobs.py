from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict

from src.schemas import JobStatus, ScrapeRequest, ScrapeResponse
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

    async def _worker(self, worker_index: int) -> None:
        log.info("job_runner started worker_index=%s", worker_index)
        while not self._stop.is_set():
            job_id = await self._queue._queue.get()
            job_record = await self._queue.get(job_id)
            if job_record is None:
                continue

            await self._queue._set(job_id, status="running", done=0)

            results: list[ScrapeResponse] = []
            try:
                completed = 0
                for page in job_record.pages:
                    worker_result = await worker_pool.submit(
                        {"job_id": new_request_id(), "request": page.model_dump(mode="json")}
                    )
                    if not worker_result.get("ok", False):
                        raise RuntimeError(worker_result.get("error", "worker_failed"))

                    results.append(ScrapeResponse.model_validate(worker_result["result"]))
                    completed += 1
                    await self._queue._set(job_id, done=completed)

                await self._queue._set(job_id, status="done", done=job_record.total, results=results)
            except Exception as e:
                await self._queue._set(job_id, status="failed", error=str(e))


job_queue = InMemoryJobQueue()


def get_job_queue() -> InMemoryJobQueue:
    return job_queue
