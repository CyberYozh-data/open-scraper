from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from src.sessions.models import (
    SessionCookieInjection,
    SessionCreateRequest,
    SessionLoginRequest,
    SessionLoginResult,
    SessionPublic,
    SessionRecord,
)
from src.sessions.store import SessionIncompatible, get_session_store
from src.utils.ids import new_request_id
from src.worker_pool import worker_pool


class StorageStateNotRevealed(ValueError):
    pass


log = logging.getLogger(__name__)


class SessionService:
    async def create(self, request: SessionCreateRequest) -> SessionRecord:
        return await get_session_store().create(request)

    async def list_all(self) -> list[SessionPublic]:
        return await get_session_store().list()

    async def get_public(self, session_id: str) -> SessionPublic:
        store = get_session_store()
        session_record = await store.get(session_id)
        return store._to_public(session_record)

    async def delete(self, session_id: str) -> None:
        await get_session_store().delete(session_id)

    async def get_storage_state(self, session_id: str, reveal: int) -> dict:
        if reveal != 1:
            raise StorageStateNotRevealed()
        session_record = await get_session_store().get(session_id)
        return session_record.storage_state or {"cookies": [], "origins": []}

    async def inject_cookies(self, session_id: str, body: SessionCookieInjection) -> dict:
        store = get_session_store()
        session_record = await store.get(session_id)
        existing_state = session_record.storage_state or {"cookies": [], "origins": []}
        merged_cookies = list(existing_state.get("cookies") or []) + list(body.cookies)
        new_state = {"cookies": merged_cookies, "origins": existing_state.get("origins") or []}
        updated = await store.update_storage_state(session_id, new_state)
        await store.set_status(session_id, "ready")
        return {
            "session_id": session_id,
            "cookies_injected": len(body.cookies),
            "storage_state_bytes": updated.storage_state_bytes,
        }

    async def login(self, session_id: str, body: SessionLoginRequest) -> SessionLoginResult:
        store = get_session_store()
        session_record = await store.get(session_id)

        async with store.lock(session_id):
            await store.set_status(session_id, "logging_in", login_script=body.script)

            job = {
                "type": "login",
                "job_id": new_request_id(),
                "session_pin": {
                    "device": session_record.device,
                    "proxy_type": session_record.proxy_type,
                    "proxy_pool_id": session_record.proxy_pool_id,
                    "proxy_geo": (
                        session_record.proxy_geo.model_dump(exclude_none=True)
                        if session_record.proxy_geo else None
                    ),
                },
                "script": body.script.model_dump(),
                "creds": dict(body.creds),
                "storage_state": session_record.storage_state,
            }
            try:
                worker_result = await worker_pool.submit(job)
            except asyncio.TimeoutError as exc:
                await store.set_status(session_id, "failed", last_error="login timed out")
                raise HTTPException(status_code=504, detail="login_timed_out") from exc
            except Exception as exc:  # pylint: disable=broad-except
                await store.set_status(session_id, "failed", last_error=f"submit failed: {exc}")
                raise

            if not worker_result.get("ok", False):
                error = worker_result.get("error", "worker_failed")
                log.warning("login worker error session_id=%s error=%s", session_id, error)
                await store.set_status(session_id, "failed", last_error=error)
                return SessionLoginResult(ok=False, error=error)

            login_result = SessionLoginResult.model_validate(worker_result["login_result"])
            if login_result.ok:
                new_state = worker_result.get("storage_state")
                if new_state is not None:
                    try:
                        await store.update_storage_state(session_id, new_state)
                    except SessionIncompatible as exc:
                        log.warning("session %s: storage_state too large: %s", session_id, exc)
                        await store.set_status(session_id, "failed", last_error=str(exc))
                        return SessionLoginResult(ok=False, error=str(exc))
                await store.set_status(session_id, "ready", last_error=None)
                log.info("login succeeded session_id=%s took_ms=%d", session_id, login_result.took_ms)
            else:
                log.warning(
                    "login script failed session_id=%s failed_step=%s error=%s",
                    session_id, login_result.failed_step_index, login_result.error,
                )
                await store.set_status(session_id, "failed", last_error=login_result.error)
            return login_result


session_service = SessionService()
