from __future__ import annotations

import logging
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException

from src.proxy.models import ProxyConfig
from src.proxy.resolver import CyberYozhAPIError, proxy_resolver
from src.schemas import (
    CountriesResponse,
    ProxyListResponse,
    ProxyResolveResponse,
    ProxyType,
    ScrapeProxyType,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _proxy_config_to_url(cfg: ProxyConfig) -> str:
    """Render a ProxyConfig as a single URL, injecting URL-encoded credentials."""
    if not cfg.username and not cfg.password:
        return cfg.server
    parts = urlsplit(cfg.server)
    user = quote(cfg.username or "", safe="")
    password = quote(cfg.password or "", safe="")
    netloc = f"{user}:{password}@{parts.netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@router.get(
    "/available",
    response_model=ProxyListResponse,
    operation_id="list_available_proxies",
)
async def list_available_proxies(proxy_type: ProxyType) -> ProxyListResponse:
    """
    List purchased proxies from the CyberYozh account for the given proxy type.

    Returns an empty list when the CyberYozh API key is not configured.
    Rotating proxies also return the pool of source proxies that back the
    rotating endpoint; passing the id of one via ``proxy_pool_id`` pins the
    scrape to that pool.
    """
    try:
        return await proxy_resolver.list_available_proxies(proxy_type)
    except CyberYozhAPIError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc


@router.get(
    "/countries",
    response_model=CountriesResponse,
    operation_id="list_proxy_countries",
)
async def list_proxy_countries() -> CountriesResponse:
    """
    Return the list of countries supported by the CyberYozh rotating
    residential proxy endpoint. Sourced from the static CyberYozh country
    file; safe to call without an API key.
    """
    return proxy_resolver.list_proxy_countries()


@router.get(
    "/resolve",
    response_model=ProxyResolveResponse,
    operation_id="resolve_proxy",
)
async def resolve_proxy(
    proxy_type: ScrapeProxyType,
    proxy_pool_id: str | None = None,
    country_code: str | None = None,
    region: str | None = None,
    city: str | None = None,
) -> ProxyResolveResponse:
    """Resolve an upstream proxy URL for a proxy_type (+ optional pool / geo).

    Reuses the same CyberYozh resolution `/scrape` uses and returns the upstream
    proxy URL so service-to-service callers (e.g. the crawler's /map) can proxy
    their own httpx fetches. `proxy_type=none` (or no proxy configured) → null.

    Security: the returned URL embeds proxy credentials. This is an internal,
    same-trust-domain helper (it is excluded from the MCP tool surface and must
    not be exposed publicly).
    """
    if proxy_type == "none":
        return ProxyResolveResponse(proxy_url=None)
    try:
        proxy_url = await _resolve_upstream_proxy_url(
            proxy_type, proxy_pool_id, country_code, region, city
        )
    except (CyberYozhAPIError, RuntimeError) as exc:
        # open_session raises bare RuntimeError for ordinary conditions like a
        # depleted pool / empty rotating credentials (see cyberyozh/provider.py);
        # surface those as 502, not an opaque 500 (mirrors worker_pool).
        # Never log the resolved URL itself — it embeds credentials.
        log.warning(
            "proxy resolve failed proxy_type=%s pool=%r geo=%r/%r/%r: %s",
            proxy_type, proxy_pool_id, country_code, region, city, exc,
        )
        raise HTTPException(
            status_code=502, detail=getattr(exc, "detail", str(exc))
        ) from exc
    return ProxyResolveResponse(proxy_url=proxy_url)


async def _resolve_upstream_proxy_url(
    proxy_type: ScrapeProxyType,
    proxy_pool_id: str | None,
    country_code: str | None,
    region: str | None,
    city: str | None,
) -> str | None:
    """Open a proxy session and render its config as a credentialed URL."""
    geo = {
        key: value
        for key, value in (
            ("country_code", country_code),
            ("region", region),
            ("city", city),
        )
        if value
    }
    session = await proxy_resolver.open_session(
        proxy_type, proxy_pool_id=proxy_pool_id, proxy_geo=geo or None
    )
    cfg = session.current_proxy()
    return _proxy_config_to_url(cfg) if cfg else None
