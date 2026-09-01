"""Sanitized read-only view of the CyberYozh v2 premium-proxy catalog.

Every handler here is a RELAY: it opens a call to `app.cyberyozh.com` carrying
this account's `CYBERYOZH_API_KEY` and returns a projection of the answer. That
made the router an uncached, unmetered front for a paid vendor API — a caller
looping `/geo/cities` with a fresh `city_name` each time drove one upstream
request per hit, spending the account's quota and using this host to anonymize
the traffic.

Three bounds, because no one of them is sufficient:

  * a TTL cache — the geo catalog is reference data that changes on the order
    of months, so serving it from memory removes the ordinary load entirely;
  * bounded parameters — `city_name` is free-form, so without a shape
    constraint the cache key space is unbounded and a caller cycling values
    walks straight past the cache;
  * a process-wide budget on UPSTREAM calls — the backstop for whatever the
    first two do not cover.

Gated at the ROUTER, never per-operation-id. The defect that put these eight
routes on the unauthenticated MCP tool surface was a rule keyed on
`operation_id` when none of them declared one — so a ninth endpoint added to
this file inherits the gate by existing, rather than by someone remembering.
The explicit ids below exist for the opposite reason: FastAPI's generated ids
are path-derived, so renaming a handler silently renames its tool.

DEPLOY ORDER IS LOAD-BEARING. yozh's `_proxy_get_safe` swallows >=400 into an
empty list and its capability probe turns one into "premium proxy unavailable",
so shipping this before that side carries a valid X-Service-Token strips
country geo from every scan and empties the admin proxy panel. Nothing RAISES
on either side; the admin path logs nothing at all, and the scan path does emit
`WARNING premium proxy capability fetch failed` — that one line is the only
thread to pull if this ships early.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.service_auth import require_service_token
from src.proxy.resolver import proxy_resolver

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/prem-proxies",
    tags=["prem-proxies"],
    dependencies=[Depends(require_service_token)],
)

# Keys the UI actually reads from session-options; everything else is dropped.
_SESSION_OPTIONS_KEYS = {"ip_filters", "session_durations", "protocols", "username_grammar"}

# Geo reference data changes on the order of months; an hour of staleness is
# invisible to the UI and removes the ordinary upstream load entirely.
_GEO_TTL_S = 3600.0
# Account state (traffic left, sub-users) does move, so it gets a short TTL —
# enough to collapse a page load's burst, not enough to show a stale balance.
_ACCOUNT_TTL_S = 30.0

# Upstream calls allowed per window, process-wide. A human using the UI spends
# a handful; an amplification loop spends this in a second and then gets 429s
# while the vendor sees nothing further.
#
# A TOKEN BUCKET, not a fixed window. A fixed window straddles: spend it at the
# end of one and again at the start of the next and a caller gets 2x inside a
# fraction of a second — measured at 120 upstream calls in 0.2s of wall time.
UPSTREAM_BUDGET = 60
_BUDGET_WINDOW_S = 60.0
_REFILL_PER_S = UPSTREAM_BUDGET / _BUDGET_WINDOW_S

# The cache must be bounded as well as rate-limited. `max_length=64` bounds a
# key's SIZE, not the COUNT: `city_name` is free-form, so a caller cycling
# values left one permanent entry per request, each holding a full vendor
# response. The budget caps the rate of growth, never the total.
CACHE_MAX = 512

_cache: "OrderedDict[tuple, tuple[float, Any]]" = OrderedDict()
# One lock per in-flight key, so a burst of identical requests makes ONE
# upstream call instead of one each. The cache only ever collapsed sequential
# traffic; the admin panel's cascading dropdowns are a burst.
_inflight: dict[tuple, asyncio.Lock] = {}
_tokens = float(UPSTREAM_BUDGET)
_refilled_at = 0.0


def reset_relay_state() -> None:
    """Drop the cache and refill the bucket. For tests and nothing else."""
    global _tokens, _refilled_at  # pylint: disable=global-statement
    _cache.clear()
    _inflight.clear()
    _tokens = float(UPSTREAM_BUDGET)
    _refilled_at = 0.0


def _client():
    return getattr(proxy_resolver, "_client_v2", None)


def _spend_upstream_token() -> bool:
    """False when the bucket is empty. Refills continuously, so there is no
    window edge to straddle."""
    global _tokens, _refilled_at  # pylint: disable=global-statement
    now = time.monotonic()
    if _refilled_at == 0.0:
        _refilled_at = now
    _tokens = min(
        float(UPSTREAM_BUDGET), _tokens + (now - _refilled_at) * _REFILL_PER_S
    )
    _refilled_at = now
    if _tokens < 1.0:
        return False
    _tokens -= 1.0
    return True


_MISS = object()


def _lookup(key: tuple, ttl: float) -> Any:
    """The cached value, or `_MISS`. A stale entry is DELETED rather than
    ignored — ignoring it left it resident forever."""
    hit = _cache.get(key)
    if hit is None:
        return _MISS
    if time.monotonic() - hit[0] >= ttl:
        _cache.pop(key, None)
        return _MISS
    _cache.move_to_end(key)
    return hit[1]


async def _relay(
    label: str,
    key: tuple,
    call: Callable[[], Awaitable[Any]],
    *,
    ttl: float,
    on_error: Any,
) -> Any:
    """Serve `key` from cache, else spend a token and ask upstream.

    An exhausted budget raises 429 rather than returning `on_error`: an empty
    list is indistinguishable from "this country has no ISPs", and the UI would
    render that silent lie instead of showing the user a retry. Upstream
    failures still fall back to `on_error` — that is a vendor outage, not a
    caller error, and it is not cached.
    """
    hit = _lookup(key, ttl)
    if hit is not _MISS:
        return hit

    lock = _inflight.setdefault(key, asyncio.Lock())
    async with lock:
        try:
            # Re-check under the lock: whoever held it first has filled the
            # cache, and the whole point is that the rest do not pay again.
            hit = _lookup(key, ttl)
            if hit is not _MISS:
                return hit

            if not _spend_upstream_token():
                log.warning("prem %s refused: upstream budget exhausted", label)
                raise HTTPException(
                    status_code=429,
                    detail="premium-proxy catalog is rate limited; retry shortly",
                )

            try:
                value = await call()
            except Exception:  # pylint: disable=broad-except
                log.exception("prem %s upstream error", label)
                return on_error

            # Stamped AFTER the await: taking `now` before it would shorten the
            # TTL by the upstream's own latency.
            _cache[key] = (time.monotonic(), value)
            while len(_cache) > CACHE_MAX:
                _cache.popitem(last=False)
            return value
        finally:
            _inflight.pop(key, None)


@router.get("/subscription", operation_id="prem_subscription")
async def subscription():
    c = _client()
    if c is None:
        return {"configured": False}
    sub = await _relay(
        "subscription", ("subscription",), c.subscription,
        ttl=_ACCOUNT_TTL_S, on_error=None,
    )
    if sub is None:
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


@router.get("/session-options", operation_id="prem_session_options")
async def session_options():
    c = _client()
    if c is None:
        return {}
    raw = await _relay(
        "session-options", ("session-options",), c.session_options,
        ttl=_GEO_TTL_S, on_error={},
    )
    return {k: v for k, v in raw.items() if k in _SESSION_OPTIONS_KEYS}


@router.get("/sub-users", operation_id="prem_sub_users")
async def sub_users():
    c = _client()
    if c is None:
        return []
    users = await _relay(
        "sub-users", ("sub-users",), c.sub_users,
        ttl=_ACCOUNT_TTL_S, on_error=[],
    )
    return [
        {"id": str(u.get("id")), "login": u.get("login"), "is_primary": bool(u.get("is_primary"))}
        for u in users
    ]


# `country_code` is an ISO 3166 alpha-2/alpha-3 code and nothing else. Pinned
# at the schema so a malformed value costs no upstream call and cannot become a
# distinct cache key; `max_length` does the same job for the free-form names.
#
# Each parameter gets its OWN Query() — a single shared instance carries the
# constraint to the first parameter bound to it and silently drops it for the
# rest, which is how `region_code` accepted a 500-character value while the
# constant claiming to bound it sat right there.
_COUNTRY_CODE_PATTERN = "^[A-Za-z]{2,3}$"
# Case-folded into the key: `US`, `us` and `Us` are one country, and three
# spellings meant three upstream calls and three cache entries for one answer.
_MAX_NAME_LEN = 64


@router.get("/geo/countries", operation_id="prem_geo_countries")
async def geo_countries(name: str | None = Query(default=None, max_length=_MAX_NAME_LEN)):
    c = _client()
    if c is None:
        return []
    return await _relay(
        "geo/countries", ("geo/countries", name), lambda: c.geo_countries(name),
        ttl=_GEO_TTL_S, on_error=[],
    )


@router.get("/geo/regions", operation_id="prem_geo_regions")
async def geo_regions(country_code: str = Query(..., pattern=_COUNTRY_CODE_PATTERN)):
    c = _client()
    if c is None:
        return []
    return await _relay(
        "geo/regions", ("geo/regions", country_code.upper()), lambda: c.geo_regions(country_code),
        ttl=_GEO_TTL_S, on_error=[],
    )


@router.get("/geo/cities", operation_id="prem_geo_cities")
async def geo_cities(
    country_code: str = Query(..., pattern=_COUNTRY_CODE_PATTERN),
    region_code: str | None = Query(default=None, max_length=_MAX_NAME_LEN),
):
    c = _client()
    if c is None:
        return []
    return await _relay(
        "geo/cities", ("geo/cities", country_code.upper(), region_code),
        lambda: c.geo_cities(country_code, region_code),
        ttl=_GEO_TTL_S, on_error=[],
    )


@router.get("/geo/zips", operation_id="prem_geo_zips")
async def geo_zips(
    country_code: str = Query(..., pattern=_COUNTRY_CODE_PATTERN),
    city_name: str | None = Query(default=None, max_length=_MAX_NAME_LEN),
):
    c = _client()
    if c is None:
        return []
    return await _relay(
        "geo/zips", ("geo/zips", country_code.upper(), city_name),
        lambda: c.geo_zips(country_code, city_name),
        ttl=_GEO_TTL_S, on_error=[],
    )


@router.get("/geo/isps", operation_id="prem_geo_isps")
async def geo_isps(
    country_code: str = Query(..., pattern=_COUNTRY_CODE_PATTERN),
    city_name: str | None = Query(default=None, max_length=_MAX_NAME_LEN),
):
    c = _client()
    if c is None:
        return []
    return await _relay(
        "geo/isps", ("geo/isps", country_code.upper(), city_name),
        lambda: c.geo_isps(country_code, city_name),
        ttl=_GEO_TTL_S, on_error=[],
    )
