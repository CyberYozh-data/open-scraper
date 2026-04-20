import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP

from src.api.router import router
from src.jobs import get_job_queue
from src.jobs import JobRunner
from src.settings import settings, setup_logging
from src.worker_pool import worker_pool, WorkerPoolConfig


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger(__name__)

    worker_pool.init(
        WorkerPoolConfig(
            workers=settings.workers,
            queue_maxsize=settings.queue_maxsize,
            job_timeout_ms=settings.job_timeout_ms,
        )
    )
    worker_pool.start()

    job_runner = JobRunner(get_job_queue(), concurrency=settings.workers)
    if settings.jobs_enabled:
        await job_runner.start()

    log.info("started workers=%d queue=%d", settings.workers, settings.queue_maxsize)

    yield

    if settings.jobs_enabled:
        await job_runner.stop()
    worker_pool.stop()


def disable_cors(app: FastAPI) -> FastAPI:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:6274",
            "http://127.0.0.1:6274",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[
            "Mcp-Session-Id",
            "mcp-session-id",
        ],
    )
    return app


def create_app() -> FastAPI:
    setup_logging(settings.log_level, tag="M")

    app = FastAPI(
        title="Open Scraper",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.include_router(router)

    mcp = FastApiMCP(app)
    mcp.mount_http()

    return app
