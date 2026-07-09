from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from src.scrape_service import (
    BatchSessionConflict,
    InternalFieldsNotAllowed,
    JobNotFound,
    QueueFull,
    scrape_service,
)
from src.schemas import (
    BatchScrapeRequest,
    JobCreateResponse,
    JobResultsResponse,
    JobStatusResponse,
    ScrapeRequest,
)
from src.sessions.store import SessionExpired, SessionIncompatible, SessionNotFound


log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/page", response_model=JobCreateResponse, operation_id="run_scrape_page")
async def scrape_page(request: ScrapeRequest) -> JobCreateResponse:
    try:
        await scrape_service.validate_page_session(request)
        job_id = await scrape_service.submit_job([request])
    except InternalFieldsNotAllowed as exc:
        raise HTTPException(
            status_code=422,
            detail="preset_meta/parser_plan are internal; use /api/v1/scrape/preset/* to scrape with a preset",
        ) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    except SessionExpired as exc:
        raise HTTPException(
            status_code=410,
            detail={"detail": "session_expired", "expired_at": exc.expired_at},
        ) from exc
    except SessionIncompatible as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QueueFull as exc:
        raise HTTPException(status_code=503, detail="queue_full") from exc
    return JobCreateResponse(job_id=job_id)


@router.post("/pages", response_model=JobCreateResponse, operation_id="run_scrape_pages")
async def scrape_pages(request: BatchScrapeRequest) -> JobCreateResponse:
    try:
        pages = await scrape_service.resolve_batch_pages(request)
        job_id = await scrape_service.submit_job(pages)
    except BatchSessionConflict as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    except InternalFieldsNotAllowed as exc:
        raise HTTPException(
            status_code=422,
            detail="preset_meta/parser_plan are internal; use /api/v1/scrape/preset/* to scrape with a preset",
        ) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    except SessionExpired as exc:
        raise HTTPException(
            status_code=410,
            detail={"detail": "session_expired", "expired_at": exc.expired_at},
        ) from exc
    except SessionIncompatible as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QueueFull as exc:
        raise HTTPException(status_code=503, detail="queue_full") from exc
    return JobCreateResponse(job_id=job_id)


@router.get("/{job_id}", response_model=JobStatusResponse, operation_id="get_job_status")
async def scrape_status(job_id: str) -> JobStatusResponse:
    try:
        meta = await scrape_service.get_job_status_or_404(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job_not_found") from exc
    return JobStatusResponse(
        job_id=meta.job_id,
        status=meta.status,
        done=meta.done,
        total=meta.total,
        error=meta.error,
    )


@router.get("/{job_id}/results", response_model=JobResultsResponse, operation_id="get_job_result")
async def scrape_results(job_id: str) -> JobResultsResponse:
    try:
        snap = await scrape_service.get_job_results_or_404(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job_not_found") from exc
    # Normalize: if no slots have been filled yet, render results as null to
    # preserve the old contract (in-progress jobs returned null, not [null, ...]).
    raw = snap.results
    results = raw if raw and any(r is not None for r in raw) else None
    return JobResultsResponse(
        job_id=snap.job_id,
        status=snap.status,
        pages=snap.pages,
        total=snap.total,
        done=snap.done,
        error=snap.error,
        results=results,
    )


@router.delete("/{job_id}", operation_id="cancel_scrape_job")
async def scrape_cancel(job_id: str) -> dict:
    """Cancel a scrape/batch job. In-flight pages finish; remaining pages are
    skipped and the job transitions to status="cancelled"."""
    try:
        cancelled = await scrape_service.request_cancel(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="job_not_found") from exc
    return {"job_id": job_id, "cancelled": cancelled}
