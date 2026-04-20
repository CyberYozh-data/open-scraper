from __future__ import annotations

from fastapi import APIRouter

from src.proxy.resolver import proxy_resolver
from src.schemas import CountriesResponse, ProxyListResponse, ProxyType

router = APIRouter()


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
    return await proxy_resolver.list_available_proxies(proxy_type)


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
