import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP

from src.api.router import router
from src.jobs import get_job_queue, job_queue
from src.jobs import JobRunner
from src.sessions.store import session_store
from src.settings import settings, setup_logging
from src.worker_pool import worker_pool, WorkerPoolConfig


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger(__name__)

    job_queue.clear()

    session_store.configure(
        max_sessions=settings.sessions_max,
        storage_state_max_bytes=settings.session_storage_state_max_bytes,
    )

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

    sessions_gc_task = asyncio.create_task(_sessions_gc_loop())
    jobs_gc_task = asyncio.create_task(_jobs_gc_loop())
    log.info("started workers=%d queue=%d", settings.workers, settings.queue_maxsize)

    yield

    for gc_task in (sessions_gc_task, jobs_gc_task):
        gc_task.cancel()
        try:
            await gc_task
        except asyncio.CancelledError:
            pass
    if settings.jobs_enabled:
        await job_runner.stop()
    worker_pool.stop()


async def _sessions_gc_loop() -> None:
    while True:
        try:
            await asyncio.sleep(60)
            await session_store.sweep_expired()
        except asyncio.CancelledError:
            return
        except Exception:  # pylint: disable=broad-except
            logging.getLogger(__name__).exception("sessions GC sweep failed")


async def _jobs_gc_loop() -> None:
    while True:
        try:
            await asyncio.sleep(60)
            evicted = await job_queue.sweep_expired()
            if evicted:
                logging.getLogger(__name__).info(
                    "jobs GC evicted %d expired result(s)", len(evicted)
                )
        except asyncio.CancelledError:
            return
        except Exception:  # pylint: disable=broad-except
            logging.getLogger(__name__).exception("jobs GC sweep failed")


def disable_cors(app: FastAPI) -> FastAPI:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:6274",
            "http://127.0.0.1:6274",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
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
