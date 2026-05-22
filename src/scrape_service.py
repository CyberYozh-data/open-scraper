from __future__ import annotations

from src.jobs import JobRecord, get_job_queue
from src.schemas import BatchScrapeRequest, ScrapeRequest
from src.sessions.store import get_session_store


class JobNotFound(KeyError):
    pass


class BatchSessionConflict(ValueError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class InternalFieldsNotAllowed(ValueError):
    pass


class ScrapeService:
    async def submit_job(self, pages: list[ScrapeRequest]) -> str:
        for page in pages:
            if page.preset_meta is not None or page.parser_plan is not None:
                raise InternalFieldsNotAllowed()
        return await get_job_queue().submit(pages)

    async def validate_page_session(self, page: ScrapeRequest) -> None:
        if page.session_id is None:
            return
        store = get_session_store()
        session_record = await store.get(page.session_id)
        store.assert_compatible_with_request(
            session_record,
            device=page.device,
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

    async def get_job_or_404(self, job_id: str) -> JobRecord:
        job_record = await get_job_queue().get(job_id)
        if job_record is None:
            raise JobNotFound(job_id)
        return job_record

    async def request_cancel(self, job_id: str) -> bool:
        return await get_job_queue().request_cancel(job_id)


scrape_service = ScrapeService()
