from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from src.proxy.resolver import proxy_resolver

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/prem-proxies", tags=["prem-proxies"])

# Keys the UI actually reads from session-options; everything else is dropped.
_SESSION_OPTIONS_KEYS = {"ip_filters", "session_durations", "protocols", "username_grammar"}


def _client():
    return getattr(proxy_resolver, "_client_v2", None)


@router.get("/subscription")
async def subscription():
    c = _client()
    if c is None:
        return {"configured": False}
    try:
        sub = await c.subscription()
    except Exception:
        log.exception("prem subscription upstream error")
        return {"configured": False}
    return {
        "configured": True,
        "provisioning_status": sub.get("provisioning_status"),
        "proxy_host": sub.get("proxy_host"),
        "proxy_port": sub.get("proxy_port"),
        "proxy_port_socks5": sub.get("proxy_port_socks5"),
        "traffic_left_mb": sub.get("traffic_left_mb"),
        "expires_at": sub.get("expires_at"),
        "plan_id": sub.get("plan_id"),
    }


@router.get("/session-options")
async def session_options():
    c = _client()
    if c is None:
        return {}
    try:
        raw = await c.session_options()
    except Exception:
        log.exception("prem session-options upstream error")
        return {}
    return {k: v for k, v in raw.items() if k in _SESSION_OPTIONS_KEYS}


@router.get("/sub-users")
async def sub_users():
    c = _client()
    if c is None:
        return []
    try:
        users = await c.sub_users()
    except Exception:
        log.exception("prem sub-users upstream error")
        return []
    return [
        {"id": str(u.get("id")), "login": u.get("login"), "is_primary": bool(u.get("is_primary"))}
        for u in users
    ]


@router.get("/geo/countries")
async def geo_countries(name: str | None = Query(default=None)):
    c = _client()
    if c is None:
        return []
    try:
        return await c.geo_countries(name)
    except Exception:
        log.exception("prem geo/countries upstream error")
        return []


@router.get("/geo/regions")
async def geo_regions(country_code: str = Query(...)):
    c = _client()
    if c is None:
        return []
    try:
        return await c.geo_regions(country_code)
    except Exception:
        log.exception("prem geo/regions upstream error")
        return []


@router.get("/geo/cities")
async def geo_cities(country_code: str = Query(...), region_code: str | None = Query(default=None)):
    c = _client()
    if c is None:
        return []
    try:
        return await c.geo_cities(country_code, region_code)
    except Exception:
        log.exception("prem geo/cities upstream error")
        return []


@router.get("/geo/zips")
async def geo_zips(country_code: str = Query(...), city_name: str | None = Query(default=None)):
    c = _client()
    if c is None:
        return []
    try:
        return await c.geo_zips(country_code, city_name)
    except Exception:
        log.exception("prem geo/zips upstream error")
        return []


@router.get("/geo/isps")
async def geo_isps(country_code: str = Query(...), city_name: str | None = Query(default=None)):
    c = _client()
    if c is None:
        return []
    try:
        return await c.geo_isps(country_code, city_name)
    except Exception:
        log.exception("prem geo/isps upstream error")
        return []
