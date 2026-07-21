from __future__ import annotations

import logging
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from src.api.service_auth import require_service_token
from src.proxy.base import ProxyConfigError
from src.proxy.models import ProxyConfig
from src.proxy.resolver import CyberYozhAPIError, proxy_resolver
from src.schemas import (
    CountriesResponse,
    PremProxyOptions,
    ProxyGeo,
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
    dependencies=[Depends(require_service_token)],
)
async def resolve_proxy(
    proxy_type: ScrapeProxyType,
    proxy_pool_id: str | None = None,
    country_code: str | None = None,
    region: str | None = None,
    city: str | None = None,
    # prem_res_rotating targeting — ignored for other proxy types. Flat query
    # params (this is a GET helper) mirroring PremProxyOptions; the crawler
    # forwards them so /map discovery fetches get the same premium targeting
    # (IP filter, sticky sessions, ISP, ZIP, sub-user) as /scrape.
    ip_filter: str | None = None,
    isp: str | None = None,
    zip_code: str | None = None,
    session_type: str | None = None,
    sticky_id: str | None = None,
    rotation_minutes: int | None = None,
    sub_user_id: str | None = None,
    protocol: str | None = None,
) -> ProxyResolveResponse:
    """Resolve an upstream proxy URL for a proxy_type (+ optional pool / geo /
    prem targeting).

    Reuses the same CyberYozh resolution `/scrape` uses and returns the upstream
    proxy URL so service-to-service callers (e.g. the crawler's /map) can proxy
    their own httpx fetches. `proxy_type=none` → null. A real proxy_type with no
    provider configured fails closed (422) rather than returning null and
    letting the caller proceed direct — it never silently degrades egress.

    Security: the returned URL embeds proxy credentials. This is an internal,
    same-trust-domain helper — it is excluded from the MCP tool surface and
    guarded by the SERVICE_TOKEN gate (X-Service-Token header, fail-closed when
    unset); it must not be exposed to untrusted callers.
    """
    if proxy_type == "none":
        return ProxyResolveResponse(proxy_url=None)
    # Validate the flat query params through the SAME models /scrape uses, so this
    # GET helper can't bypass their Literal/range/charset constraints (e.g. a
    # bogus ip_filter, an out-of-range rotation_minutes, or a '-'-injecting
    # country_code / sticky_id). model_dump(exclude_unset) keeps only the keys the
    # caller actually passed — model defaults must not leak into the upstream
    # username (the v2 provider applies its own defaults).
    try:
        geo = ProxyGeo(country_code=country_code, region=region, city=city)
        raw_prem = {
            key: value
            for key, value in (
                ("ip_filter", ip_filter),
                ("isp", isp),
                ("zip", zip_code),
                ("session_type", session_type),
                ("sticky_id", sticky_id),
                ("rotation_minutes", rotation_minutes),
                ("sub_user_id", sub_user_id),
                ("protocol", protocol),
            )
            if value is not None
        }
        prem_proxy_options = (
            PremProxyOptions(**raw_prem).model_dump(exclude_unset=True, exclude_none=True)
            if raw_prem
            else None
        ) or None
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    try:
        proxy_url = await _resolve_upstream_proxy_url(
            proxy_type, proxy_pool_id, geo.country_code, geo.region, geo.city, prem_proxy_options
        )
    except ProxyConfigError as exc:
        # Well-formed params the catalog can't satisfy (e.g. a region name from
        # another country) — user input, 422 like the validation above.
        log.warning(
            "proxy resolve rejected proxy_type=%s pool=%r geo=%r/%r/%r prem=%r: %s",
            proxy_type, proxy_pool_id, country_code, region, city, prem_proxy_options, exc,
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (CyberYozhAPIError, RuntimeError) as exc:
        # open_session raises bare RuntimeError for ordinary conditions like a
        # depleted pool / empty rotating credentials (see cyberyozh/provider.py);
        # surface those as 502, not an opaque 500 (mirrors the scrape task's proxy-error mapping).
        # Never log the resolved URL itself — it embeds credentials.
        log.warning(
            "proxy resolve failed proxy_type=%s pool=%r geo=%r/%r/%r prem=%r: %s",
            proxy_type, proxy_pool_id, country_code, region, city, prem_proxy_options, exc,
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
    prem_proxy_options: dict | None = None,
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
        proxy_type,
        proxy_pool_id=proxy_pool_id,
        proxy_geo=geo or None,
        prem_proxy_options=prem_proxy_options,
    )
    cfg = session.current_proxy()
    return _proxy_config_to_url(cfg) if cfg else None
