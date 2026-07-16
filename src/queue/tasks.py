"""taskiq task definitions. Invariant: one delivery = one slot write — the task
body never raises past its guard; any failure becomes an error slot. No taskiq
retry middleware for scrape tasks (application retries already exist; a poison
task in endless retry would break job convergence)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import traceback
from typing import Any

from taskiq import Context, TaskiqDepends

from src.browser.camoufox_runner import CamoufoxRunner
from src.browser.login_runner import LoginRunner
from src.proxy.base import ProxyConfigError
from src.proxy.resolver import proxy_resolver
from src.queue import scrape_runner
from src.queue.broker import broker
from src.queue.reaper import reclaim_once
from src.queue.store import TERMINAL_STATUSES, JobNotInStore, get_job_store, pack_payload
from src.sessions.models import LoginScript
from src.sessions.store import (
    SessionExpired,
    SessionIncompatible,
    SessionNotFound,
    get_session_store,
)
from src.settings import settings
from src.utils.ids import new_request_id

log = logging.getLogger(__name__)

LOGIN_PAYLOAD_KEY = "login:{}:payload"
LOGIN_RESULT_KEY = "login:{}:result"
LOGIN_KEY_TTL_S = 600


def _is_successful_status(status_code: int | None) -> bool:
    """200-399 is the persist gate (moved from src/jobs.py)."""
    return status_code is not None and 200 <= int(status_code) < 400


def make_error_payload(page: dict[str, Any], error: str) -> dict[str, Any]:
    """ScrapeResponse-shaped error placeholder (moved from src/jobs.py
    _create_error_response, dict-native)."""
    return {
        "request_id": new_request_id(),
        "took_ms": 0,
        "meta": {
            "url": str(page.get("url")),
            # Default the Literal-typed fields so an empty page dict (the
            # missing-payload branch) still validates as ScrapeMeta on read-back.
            "device": page.get("device") or "desktop",
            "proxy_type": page.get("proxy_type") or "none",
            "proxy_pool_id": page.get("proxy_pool_id"),
            "status_code": None,
            "retries": 0,
            # Hard worker failure (timeout / exception / missing payload): the
            # fetch did not succeed, so be honest rather than inheriting the
            # ScrapeMeta default of True.
            "fetch_ok": False,
        },
        # First-class reason for clients; kept in warnings too so readers
        # that predate the `error` field still see it.
        "error": error,
        "warnings": [error],
    }


# --- worker-state access seams (real values set by worker.py startup hook) ---
async def _get_runner(context: Context, engine: str = "chromium"):
    """Return the runner for `engine`.

    Camoufox is per-request (fresh fingerprint — strongest anti-detect posture,
    zero idle RAM); native Playwright engines are warm and lazily registered in
    state.runners so the lifecycle loop can retire/restart them independently."""
    if engine == "camoufox":
        return CamoufoxRunner(timeout_ms=settings.request_timeout_ms)
    # Imported lazily (not at module scope): worker.py imports this module at
    # load time, so a top-level `from src.queue.worker import _new_runner` is a
    # circular import that crashes the worker entrypoint (worker:broker).
    from src.queue.worker import _new_runner

    state = context.state
    # Caller holds state.browser_lock (see _browser_guard); do NOT re-acquire — asyncio.Lock is not reentrant.
    runner = state.runners.get(engine)
    if runner is None:
        runner = _new_runner(engine)
        state.runners[engine] = runner
        state.last_activity[engine] = asyncio.get_running_loop().time()
        state.pages_since_launch[engine] = 0
    await runner.start()  # idempotent; lazy-launches the browser if not started
    return runner


def _browser_guard(context: Context):
    """Hold the worker's browser lock during a scrape so the lifecycle loop
    can't close the browser mid-task. Scrapes never contend with each other
    here — the worker runs with --max-async-tasks 1 (one task per process),
    concurrency scales via processes — so the lock only serializes a scrape
    against browser teardown (idle shutdown / retirement / drain). Null
    context under unit tests (no lock or unresolved Dependency when called
    directly)."""
    state = getattr(context, "state", None)
    lock = getattr(state, "browser_lock", None) if state is not None else None
    return lock if lock is not None else contextlib.nullcontext()


def _record_page(context: Context, engine: str = "chromium") -> None:
    """Bump the per-engine retirement counter + idle clock. No-op when running
    outside a real worker (InMemoryBroker tests lack these state fields, or
    context is an unresolved Dependency when the task is called directly).

    Task 5 will pass the real engine; the default keeps existing call sites
    (tasks.py:176 and :222) working without change."""
    state = getattr(context, "state", None)
    if state is None or not hasattr(state, "pages_since_launch"):
        return
    state.pages_since_launch[engine] = state.pages_since_launch.get(engine, 0) + 1
    state.last_activity[engine] = asyncio.get_running_loop().time()


async def _scrape_with_session(runner, request_id: str, page: dict[str, Any]) -> dict[str, Any]:
    """Session-pinned page: storage_state relay via the session store under the
    session lock. Mirrors old JobRunner._process_page session branch."""
    session_id = page["session_id"]
    store = get_session_store()
    async with store.lock(session_id):
        record = await store.get(session_id)
        if page.get("cookies"):
            log.warning("session_id=%s came with cookies — ignoring to honor session pin", session_id)
            page = {**page, "cookies": None}
        # The session's pinned viewport is authoritative: inject it so this
        # scrape presents the same screen size the login used (and every other
        # scrape on the session). A diverging request viewport was already
        # rejected upstream (assert_compatible_with_request); an unset one
        # inherits here. None means "use the engine's desktop default".
        page = {
            **page,
            "viewport": record.viewport.model_dump() if record.viewport else None,
        }
        envelope = await scrape_runner.run_scrape(runner, request_id, page, record.storage_state)
        if envelope.get("ok"):
            new_state = envelope.get("storage_state")
            status_code = envelope.get("result", {}).get("meta", {}).get("status_code")
            if new_state is not None and _is_successful_status(status_code):
                await store.update_storage_state(session_id, new_state)
        return envelope


async def _kiq_chain_next(store, job_id: str, page_index: int) -> None:
    nxt = await store.get_chain_next(job_id, page_index)
    if nxt is not None:
        await scrape_page_task.kiq(job_id, nxt)


@broker.task(task_name="scrape_page")
async def scrape_page_task(
    job_id: str,
    page_index: int,
    context: Context = TaskiqDepends(),
) -> None:
    store = get_job_store()
    meta = await store.get_meta(job_id)
    if meta is None:
        log.warning("scrape_page: job %s gone (orphan past TTL), dropping", job_id)
        return
    if meta.status in TERMINAL_STATUSES or meta.cancelled:
        return  # cancel_fill already stubbed our slot; chains stop here

    # Redelivery fast-forward: slot already done -> only re-issue the chain.
    if await store.slot_filled(job_id, page_index):
        await _kiq_chain_next(store, job_id, page_index)
        return

    await store.mark_running(job_id)
    page = await store.get_page(job_id, page_index)
    if page is None:
        # Near-impossible (the pages hash is created with meta and shares its
        # TTL), but if the payload is gone we must still write a slot so the job
        # converges instead of hanging in "running" forever.
        log.error("scrape_page: job %s page %d payload missing", job_id, page_index)
        try:
            await store.write_slot(
                job_id, page_index, pack_payload(make_error_payload({}, "page payload missing")),
            )
        except JobNotInStore:
            return
        await _kiq_chain_next(store, job_id, page_index)
        return

    request_id = new_request_id()

    async def _scrape() -> dict[str, Any]:
        async with _browser_guard(context):
            runner = await _get_runner(context, page.get("browser_engine", "chromium"))
            if page.get("session_id"):
                return await _scrape_with_session(runner, request_id, page)
            return await scrape_runner.run_scrape(runner, request_id, page, None)

    try:
        # asyncio.wait_for (not asyncio.timeout): repo floors python at 3.10.
        envelope = await asyncio.wait_for(_scrape(), timeout=settings.page_task_timeout_s)
    except (SessionNotFound, SessionExpired, SessionIncompatible) as exc:
        envelope = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    except asyncio.TimeoutError:
        envelope = {"ok": False, "error": f"page task exceeded {settings.page_task_timeout_s}s"}
    except Exception as exc:  # noqa: BLE001 — the one-delivery-one-slot guard
        log.exception("scrape_page: unexpected failure job=%s page=%d request_id=%s",
                      job_id, page_index, request_id)
        envelope = {"ok": False, "error": str(exc) or type(exc).__name__}
    finally:
        _record_page(context, page.get("browser_engine", "chromium") if page is not None else "chromium")

    payload = (
        envelope["result"] if envelope.get("ok")
        else make_error_payload(page, envelope.get("error", "worker_failed"))
    )
    try:
        await store.write_slot(job_id, page_index, pack_payload(payload))
    except JobNotInStore:
        log.warning("scrape_page: job %s vanished before slot write", job_id)
        return

    meta = await store.get_meta(job_id)
    if meta is not None and not meta.cancelled:
        await _kiq_chain_next(store, job_id, page_index)


@broker.task(task_name="session_login")
async def login_task(login_id: str, context: Context = TaskiqDepends()) -> None:
    """Payload travels via a GETDEL'd Redis key, not the stream message, so
    credentials never sit in the (AOF-persisted) stream."""
    store = get_job_store()
    raw = await store.client.getdel(LOGIN_PAYLOAD_KEY.format(login_id))
    result: dict[str, Any]
    if raw is None:
        # The payload is GETDEL-consumed on first delivery, so a redelivery
        # (worker died mid-login) finds nothing. Credentials deliberately never
        # touch the stream, so a login is not retryable — say so plainly.
        log.warning("login %s not retryable (payload already consumed) — resubmit", login_id)
        result = {"ok": False, "error": "login not retryable after worker restart — resubmit"}
    else:
        job = json.loads(raw)
        try:
            async with _browser_guard(context):
                # Bound by login_task_timeout_s, not page_task_timeout_s, so the
                # worker abandons in lockstep with the API's wait — otherwise an
                # orphaned login could write storage_state after the API already
                # declared the session failed.
                result = await asyncio.wait_for(
                    _run_login(await _get_runner(context), job),
                    timeout=settings.login_task_timeout_s,
                )
        except Exception as exc:  # noqa: BLE001 — a login_id always gets a result key
            log.exception("login task failed login_id=%s", login_id)
            result = {"ok": False, "error": str(exc) or type(exc).__name__}
        finally:
            _record_page(context)
    await store.client.set(
        LOGIN_RESULT_KEY.format(login_id),
        json.dumps(result, separators=(",", ":")),
        ex=LOGIN_KEY_TTL_S,
    )


async def _run_login(runner, job: dict[str, Any]) -> dict[str, Any]:
    """Replay a login script using the given runner. Faithful move of
    worker_pool.py _handle_login_job body (second copy, lines 284-377).

    Job envelope keys: session_pin, script, creds, storage_state (optional).
    Returns {"ok": True, "login_result": ..., "storage_state": ...} on success
    or      {"ok": False, "error": ..., "traceback": ...} on failure.
    """
    session_pin = job["session_pin"]
    script_dict = job["script"]
    creds = job["creds"]
    storage_state = job.get("storage_state")

    page = None
    context = None
    bridge_cm = None
    try:
        await runner.start()
        # Same default-country rule as the scrape path, so the login handshake
        # and the session's scrapes present one country (exit + timezone/locale).
        proxy_geo = scrape_runner.apply_default_proxy_country(
            session_pin["proxy_type"], session_pin.get("proxy_geo") or None
        )
        session = await proxy_resolver.open_session(
            proxy_type=session_pin["proxy_type"],
            proxy_pool_id=session_pin.get("proxy_pool_id"),
            proxy_geo=proxy_geo,
        )
        proxy_cfg = session.current_proxy()

        # Chromium can't speak authenticated SOCKS5 directly; the runner
        # exposes resolve_proxy() to spawn a local HTTP-to-SOCKS5 bridge
        # when needed. Re-use the same helper that scrape paths use so
        # login works for every proxy type a scrape works for.
        effective_proxy, bridge_cm = await runner.resolve_proxy(proxy_cfg)

        context = await runner._new_context(
            device=session_pin["device"],
            proxy=effective_proxy,
            headers=None,
            block_assets=False,
            proxy_geo=proxy_geo,
            render=True,
            storage_state=storage_state,
            viewport=session_pin.get("viewport"),
        )
        page = await context.new_page()
        try:
            from playwright_stealth import Stealth  # type: ignore
            await Stealth().apply_stealth_async(page)
        except Exception:  # pylint: disable=broad-except
            pass

        login_runner = LoginRunner()
        script = LoginScript.model_validate(script_dict)
        result, new_state = await login_runner.replay(
            page=page, context=context, script=script, creds=creds,
        )

        return {
            "ok": True,
            "login_result": result.model_dump(),
            "storage_state": new_state,
        }
    except ProxyConfigError as exc:
        # User input (unsatisfiable proxy targeting in the session pin), not a
        # code failure — no traceback-bearing crash envelope.
        log.warning("proxy config error in login: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # pylint: disable=broad-except
        err_text = str(exc)
        tb_text = traceback.format_exc(limit=20)
        # Defensive: redact any credential value from the error surface — Playwright
        # error messages may echo the substituted field value.
        for cred_value in creds.values():
            if cred_value:
                err_text = err_text.replace(cred_value, "***")
                tb_text = tb_text.replace(cred_value, "***")
        return {
            "ok": False,
            "error": err_text,
            "traceback": tb_text,
        }
    finally:
        try:
            if page is not None:
                await page.close()
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            if context is not None:
                await context.close()
        except Exception:  # pylint: disable=broad-except
            pass
        if bridge_cm is not None:
            try:
                await bridge_cm.__aexit__(None, None, None)
            except Exception:  # pylint: disable=broad-except
                pass


@broker.task(task_name="reclaim_stale", schedule=[{"interval": 60, "schedule_id": "reclaim_stale"}])
async def reclaim_stale_task() -> None:
    """Periodic reclaim of stale pending stream entries (a worker died mid-task).

    Fired every 60s by the taskiq scheduler (`taskiq scheduler
    src.queue.scheduler:scheduler`), which replaces the old in-process reaper
    loop: the scheduler — a single instance — enqueues this task and a worker
    runs it, so it fires exactly once per interval regardless of API/worker
    replica count, instead of once per API process.
    """
    n = await reclaim_once()
    if n:
        log.warning("reclaimed %d stale pending task(s)", n)
