from __future__ import annotations

import logging

from fastapi import HTTPException

from src.proxy.base import ProxySession
from src.proxy.countries import COUNTRIES
from src.proxy.cyberyozh.client import CyberYozhClient
from src.proxy.cyberyozh.provider import (
    CyberYozhProxyProvider,
    get_category_proxy,
    normalize_proxy_raw_type,
)
from src.proxy.cyberyozh.session import CyberYozhSession
from src.schemas import (
    CountriesResponse,
    CountryItem,
    ProxyItem,
    ProxyListResponse,
    ProxyType,
)
from src.settings import settings

log = logging.getLogger(__name__)

_ACCESS_FILTER: dict[str, str] = {
    "mobile_shared": "shared",
    "mobile": "private",
    "res_static": "private",
    "dc_static": "private",
}


class DirectSession:
    def max_attempts(self) -> int:
        return 1

    def current_proxy(self):
        return None

    async def on_failure(self, failure):  # noqa: ARG002
        return False


class ProxyResolver:
    def __init__(self) -> None:
        api_key = settings.cyberyozh_api_key.get_secret_value() if settings.cyberyozh_api_key else ""
        if api_key:
            self._client = CyberYozhClient(
                base_url=f"{settings.cyberyozh_base_url.rstrip('/')}/api/v1",
                api_key=api_key,
            )
        else:
            self._client = None

    async def open_session(
        self,
        proxy_type: str | None,
        proxy_pool_id: str | None,
        proxy_geo: dict[str, str] | None = None
    ) -> ProxySession:
        if not proxy_type or proxy_type == "none" or self._client is None:
            return DirectSession()

        provider = CyberYozhProxyProvider(
            client=self._client,
            geo=proxy_geo
        )

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw=str(proxy_type),
            proxy_pool_id=proxy_pool_id,
        )
        return await session.init()

    async def list_available_proxies(self, proxy_type: ProxyType) -> ProxyListResponse:
        if self._client is None:
            return ProxyListResponse(
                proxy_type=proxy_type,
                category="",
                configured=False,
                items=[],
            )

        category = get_category_proxy(normalize_proxy_raw_type(proxy_type))

        try:
            proxies = await self._client.proxy_history(category=category, expired=False)
        except Exception as exc:
            log.exception("failed to fetch proxy_history for category=%s", category)
            raise HTTPException(status_code=502, detail=f"cyberyozh_api_error: {exc}") from exc

        access_filter = _ACCESS_FILTER.get(proxy_type)
        items = [
            ProxyItem(
                id=str(p.id),
                url=p.url,
                status=p.status,
                expired=p.expired,
                host=p.connection_host,
                port=p.connection_port,
                access_type=p.access_type,
            )
            for p in proxies
            if access_filter is None or p.access_type == access_filter
        ]

        return ProxyListResponse(
            proxy_type=proxy_type,
            category=category,
            configured=True,
            items=items,
        )

    def list_proxy_countries(self) -> CountriesResponse:
        return CountriesResponse(
            countries=[CountryItem(code=c.code, name=c.name) for c in COUNTRIES]
        )


proxy_resolver = ProxyResolver()
