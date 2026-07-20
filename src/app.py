import logging
import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP

from src.api import prem_proxies
from src.api.router import router
from src.api.sessions import router as sessions_router
from src.queue.broker import broker
from src.queue.store import get_job_store, init_job_store
from src.sessions.store import get_session_store, init_session_store
from src.settings import settings, setup_logging

_LEGACY_ENVS = ("JOBS_ENABLED", "JOB_RESULT_MAX", "JOB_TIMEOUT_MS")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger(__name__)
    for legacy in _LEGACY_ENVS:
        if os.environ.get(legacy):
            log.warning("env %s is no longer used after the taskiq migration", legacy)

    init_job_store()
    init_session_store(get_job_store().client)  # configured from settings inside
    if not broker.is_worker_process:
        await broker.startup()

    # Stale-pending reclaim now runs as the `reclaim_stale` scheduled task,
    # driven by the taskiq scheduler process — not an in-process loop here.
    background = [asyncio.create_task(_sessions_gc_loop())]
    log.info("api up: redis=%s queue_maxsize=%d", settings.redis_url, settings.queue_maxsize)

    yield

    for task in background:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if not broker.is_worker_process:
        await broker.shutdown()


async def _sessions_gc_loop() -> None:
    while True:
        try:
            await asyncio.sleep(60)
            await get_session_store().sweep_expired()
        except asyncio.CancelledError:
            return
        except Exception:  # pylint: disable=broad-except
            logging.getLogger(__name__).exception("sessions GC sweep failed")


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


def mcp_excluded_operations() -> list[str]:
    """Operations kept off the (unauthenticated) MCP tool surface.

    The REST routes gate these behind SERVICE_TOKEN, but the MCP transport
    carries no such header, so they must not be advertised as agent tools:
      - resolve_proxy: returns upstream proxy credentials (the crawler's /map).
      - every /sessions op (CRIT-02): create/read/list/mutate/login/delete an
        authenticated browser session — never an agent tool.

    The session ops are derived from the router so a newly added session route
    can't silently drift onto the MCP surface.
    """
    session_ops = [
        route.operation_id
        for route in sessions_router.routes
        if getattr(route, "operation_id", None)
    ]
    return ["resolve_proxy", *session_ops]


def create_app() -> FastAPI:
    setup_logging(settings.log_level, tag="M")

    app = FastAPI(
        title="Open Scraper",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(prem_proxies.router)

    mcp = FastApiMCP(app, exclude_operations=mcp_excluded_operations())
    mcp.mount_http()

    return app
