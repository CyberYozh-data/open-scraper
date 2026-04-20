from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.jobs import get_job_queue
from src.schemas import (
    BatchScrapeRequest,
    JobCreateResponse,
    JobResultsResponse,
    JobStatusResponse,
    ScrapeRequest,
)

router = APIRouter()


@router.post("/page", response_model=JobCreateResponse, operation_id="run_scrape_page")
async def scrape_page(request: ScrapeRequest) -> JobCreateResponse:
    job_id = await get_job_queue().submit([request])
    return JobCreateResponse(job_id=job_id)


@router.post("/pages", response_model=JobCreateResponse, operation_id="run_scrape_pages")
async def scrape_pages(request: BatchScrapeRequest) -> JobCreateResponse:
    job_id = await get_job_queue().submit(request.pages)
    return JobCreateResponse(job_id=job_id)


@router.get("/{job_id}", response_model=JobStatusResponse, operation_id="get_job_status")
async def scrape_status(job_id: str) -> JobStatusResponse:
    job_record = await get_job_queue().get(job_id)
    if job_record is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    return JobStatusResponse(
        job_id=job_record.job_id,
        status=job_record.status,
        done=job_record.done,
        total=job_record.total,
        error=job_record.error,
    )


@router.get("/{job_id}/results", response_model=JobResultsResponse, operation_id="get_job_result")
async def scrape_results(job_id: str) -> JobResultsResponse:
    job_queue = get_job_queue()
    job_record = await job_queue.get(job_id)
    if job_record is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    return JobResultsResponse(
        job_id=job_record.job_id,
        status=job_record.status,
        pages=job_record.pages,
        total=job_record.total,
        done=job_record.done,
        error=job_record.error,
        results=job_record.results,
    )
