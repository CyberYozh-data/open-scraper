from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.service_auth import require_service_token
from src.sessions.service import StorageStateNotRevealed, session_service
from src.sessions.models import (
    SessionCookieInjection,
    SessionCreateRequest,
    SessionCreated,
    SessionList,
    SessionLoginRequest,
    SessionLoginResult,
    SessionPublic,
)
from src.sessions.store import (
    SessionExpired,
    SessionIncompatible,
    SessionNotFound,
)


log = logging.getLogger(__name__)

# CRIT-02: every /sessions operation creates, reveals, mutates, logs into, or
# deletes an authenticated browser session (cookies + storage_state). None of it
# is safe for an anonymous caller — without ownership, a leaked/enumerated
# session_id is a full account hijack. Gate the whole surface behind the shared
# SERVICE_TOKEN (fail-closed), same as /proxies/resolve. Per-owner ownership is
# a separate follow-up for when a multi-tenant consumer actually exists.
router = APIRouter(dependencies=[Depends(require_service_token)])


@router.post("", response_model=SessionCreated, operation_id="create_session")
async def create_session(request: SessionCreateRequest) -> SessionCreated:
    try:
        session_record = await session_service.create(request)
    except SessionIncompatible as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SessionCreated(session_id=session_record.session_id, expires_at=session_record.expires_at)


@router.get("", response_model=SessionList, operation_id="list_sessions")
async def list_sessions() -> SessionList:
    items = await session_service.list_all()
    return SessionList(items=items)


@router.get("/{session_id}", response_model=SessionPublic, operation_id="get_session")
async def get_session(session_id: str) -> SessionPublic:
    try:
        return await session_service.get_public(session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    except SessionExpired as exc:
        raise HTTPException(
            status_code=410,
            detail={"detail": "session_expired", "expired_at": exc.expired_at},
        ) from exc


async def get_session_storage_state(
    session_id: str,
    reveal: int = Query(0, ge=0, le=1, description="Set 1 to return cookies + localStorage."),
) -> dict:
    # INTENTIONALLY NOT MOUNTED (no @router decorator, not added anywhere): this
    # reveals raw cookies + localStorage and has no route. If you ever wire it
    # up, mount it on THIS router so it inherits the require_service_token gate —
    # never on a separate ungated router.
    try:
        return await session_service.get_storage_state(session_id, reveal)
    except StorageStateNotRevealed as exc:
        raise HTTPException(
            status_code=400,
            detail="missing required query param ?reveal=1 — cookies are sensitive",
        ) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    except SessionExpired as exc:
        raise HTTPException(
            status_code=410,
            detail={"detail": "session_expired", "expired_at": exc.expired_at},
        ) from exc


@router.delete("/{session_id}", operation_id="delete_session")
async def delete_session(session_id: str) -> dict:
    try:
        await session_service.delete(session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    return {"session_id": session_id, "deleted": True}


@router.post("/{session_id}/cookies", operation_id="inject_session_cookies")
async def inject_session_cookies(
    session_id: str, body: SessionCookieInjection
) -> dict:
    try:
        return await session_service.inject_cookies(session_id, body)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    except SessionExpired as exc:
        raise HTTPException(
            status_code=410,
            detail={"detail": "session_expired", "expired_at": exc.expired_at},
        ) from exc


@router.post(
    "/{session_id}/login",
    response_model=SessionLoginResult,
    operation_id="login_session",
)
async def login_session(
    session_id: str, body: SessionLoginRequest
) -> SessionLoginResult:
    try:
        return await session_service.login(session_id, body)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    except SessionExpired as exc:
        raise HTTPException(
            status_code=410,
            detail={"detail": "session_expired", "expired_at": exc.expired_at},
        ) from exc
