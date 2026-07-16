from __future__ import annotations

import asyncio
import logging

from src.queue.broker import QUEUE_NAME, is_inmemory_broker
from src.queue.store import TERMINAL_STATUSES, JobMeta, JobSnapshot, get_job_store, pack_payload
from src.queue.tasks import make_error_payload, scrape_page_task
from src.schemas import BatchScrapeRequest, ScrapeRequest, ScrapeResponse
from src.settings import settings
from src.sessions.store import get_session_store

log = logging.getLogger(__name__)


class JobNotFound(KeyError):
    pass


class QueueFull(RuntimeError):
    """Submit-time backpressure: stream depth exceeded QUEUE_MAXSIZE."""


class BatchSessionConflict(ValueError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class InternalFieldsNotAllowed(ValueError):
    pass


def plan_chains(pages: list[ScrapeRequest]) -> tuple[dict[int, int], list[int]]:
    """Group page indexes by session_id: pages sharing a session form an
    index-ordered chain. Returns (chain_next map, head indexes to enqueue).
    Non-session pages are their own heads (pure fan-out)."""
    chain_next: dict[int, int] = {}
    heads: list[int] = []
    last_by_session: dict[str, int] = {}
    for i, page in enumerate(pages):
        sid = page.session_id
        if sid is None:
            heads.append(i)
            continue
        if sid in last_by_session:
            chain_next[last_by_session[sid]] = i
        else:
            heads.append(i)
        last_by_session[sid] = i
    return chain_next, heads


class ScrapeService:
    async def _enqueue(self, pages: list[ScrapeRequest]) -> str:
        store = get_job_store()
        if settings.queue_maxsize > 0 and not is_inmemory_broker():
            depth = await store.queue_depth(QUEUE_NAME)
            if depth > settings.queue_maxsize:
                raise QueueFull(f"queue depth {depth} > {settings.queue_maxsize}")
        chain_next, heads = plan_chains(pages)
        job_id = await store.create(pages, chain_next=chain_next)
        try:
            for index in heads:
                await scrape_page_task.kiq(job_id, index)
        except Exception:  # noqa: BLE001
            # A partial fan-out would otherwise leave the job hung non-terminal
            # until its 24h safety TTL (the un-enqueued slots never fill and
            # nothing is on the stream to reclaim). Converge it to cancelled so
            # a polling caller sees a terminal state, then surface the failure.
            log.exception("enqueue failed for job %s; cancelling the partial job", job_id)
            await self._cancel_unfilled(job_id)
            raise
        return job_id

    async def _cancel_unfilled(self, job_id: str) -> None:
        store = get_job_store()
        snap = await store.get_full(job_id)
        if snap is not None:
            await store.request_cancel(job_id, stub_payloads=self._stubs_for_unfilled(snap, "enqueue failed"))

    async def submit_job(self, pages: list[ScrapeRequest]) -> str:
        for page in pages:
            if page.preset_meta is not None or page.parser_plan is not None:
                raise InternalFieldsNotAllowed()
        return await self._enqueue(pages)

    async def submit_job_internal(self, pages: list[ScrapeRequest]) -> str:
        """Preset/search path: materialized requests carry internal fields."""
        return await self._enqueue(pages)

    async def validate_page_session(self, page: ScrapeRequest) -> None:
        if page.session_id is None:
            return
        store = get_session_store()
        session_record = await store.get(page.session_id)
        store.assert_compatible_with_request(
            session_record,
            device=page.device,
            viewport=page.viewport,
            proxy_type=page.proxy_type,
            proxy_pool_id=page.proxy_pool_id,
            proxy_geo=page.proxy_geo,
            cookies=page.cookies,
        )

    async def resolve_batch_pages(self, request: BatchScrapeRequest) -> list[ScrapeRequest]:
        pages = list(request.pages)
        if request.session_id is not None:
            for page_req in pages:
                if page_req.session_id is not None and page_req.session_id != request.session_id:
                    raise BatchSessionConflict(
                        f"per-page session_id={page_req.session_id} conflicts with "
                        f"batch-level session_id={request.session_id}"
                    )
            pages = [
                page_req.model_copy(update={"session_id": request.session_id})
                for page_req in pages
            ]
        for page in pages:
            await self.validate_page_session(page)
        return pages

    async def get_job_status_or_404(self, job_id: str) -> JobMeta:
        meta = await get_job_store().get_meta(job_id)
        if meta is None:
            raise JobNotFound(job_id)
        return meta

    async def get_job_results_or_404(self, job_id: str) -> JobSnapshot:
        snap = await get_job_store().get_full(job_id)
        if snap is None:
            raise JobNotFound(job_id)
        return snap

    @staticmethod
    def _stubs_for_unfilled(snap: JobSnapshot, reason: str) -> dict[int, bytes]:
        """Error-stub payloads for every slot still unfilled — used to converge
        a job (cancel, or enqueue-failure) without leaving null slots open."""
        return {
            i: pack_payload(make_error_payload(p.model_dump(mode="json"), reason))
            for i, (p, r) in enumerate(zip(snap.pages, snap.results))
            if r is None
        }

    async def request_cancel(self, job_id: str) -> bool:
        store = get_job_store()
        snap = await store.get_full(job_id)
        if snap is None:
            raise JobNotFound(job_id)
        return await store.request_cancel(job_id, stub_payloads=self._stubs_for_unfilled(snap, "cancelled"))

    async def run_and_wait(
        self, pages: list[ScrapeRequest], *, timeout_s: float | None = None
    ) -> list[ScrapeResponse | None]:
        """Same contract as before: poll until terminal, return results
        (null-padded for any still-unfilled slot on deadline)."""
        store = get_job_store()
        job_id = await self._enqueue(pages)
        if timeout_s is None:
            # +60s of queue-delivery slack above the per-task ceiling that
            # already bounds each page internally.
            timeout_s = settings.page_task_timeout_s + 60
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            meta = await store.get_meta(job_id)
            if meta is not None and meta.status in TERMINAL_STATUSES:
                break
            await asyncio.sleep(0.15)
        snap = await store.get_full(job_id)
        return snap.results if snap else []


scrape_service = ScrapeService()
