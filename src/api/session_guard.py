from __future__ import annotations

from fastapi import HTTPException

from src.schemas import ScrapeRequest
from src.sessions.store import (
    SessionExpired,
    SessionIncompatible,
    SessionNotFound,
    get_session_store,
)


async def validate_session_id(req: ScrapeRequest) -> None:
    """Validate `req.session_id` against the session store.

    Used by both the raw scrape path and the preset-scrape path. For the
    preset path the caller passes the *materialized* ScrapeRequest, so the
    device/proxy/geo the compatibility check reads are the ones the preset
    actually produced.

    No-op when `req.session_id` is None. Raises HTTPException: 404
    (session_not_found), 410 (session_expired), 422 (session_incompatible).
    """
    if req.session_id is None:
        return
    store = get_session_store()
    try:
        rec = await store.get(req.session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    except SessionExpired as exc:
        raise HTTPException(
            status_code=410,
            detail={"detail": "session_expired", "expired_at": exc.expired_at},
        ) from exc
    try:
        store.assert_compatible_with_request(
            rec,
            device=req.device,
            proxy_type=req.proxy_type,
            proxy_pool_id=req.proxy_pool_id,
            proxy_geo=req.proxy_geo,
            cookies=req.cookies,
        )
    except SessionIncompatible as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
