from __future__ import annotations

import logging

from src.proxy.base import ProxyConfigError, ProxySession
from src.proxy.countries import COUNTRIES
from src.proxy.cyberyozh.client import CyberYozhClient
from src.proxy.cyberyozh.client_v2 import CyberYozhV2Client
from src.proxy.cyberyozh.provider import (
    CyberYozhProxyProvider,
    get_category_proxy,
    normalize_proxy_raw_type,
)
from src.proxy.cyberyozh.provider_v2 import PremProxyProvider, PremProxySession
from src.proxy.cyberyozh.session import CyberYozhSession
from src.schemas import (
    CountriesResponse,
    CountryItem,
    ProxyItem,
    ProxyListResponse,
    ProxyType,
)
from src.settings import settings
from src.utils.redaction import redact_url

log = logging.getLogger(__name__)


class CyberYozhAPIError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


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
            self._client_v2 = CyberYozhV2Client(
                base_url=f"{settings.cyberyozh_base_url.rstrip('/')}/api/v2/rotating-proxies",
                api_key=api_key,
            )
        else:
            self._client = None
            self._client_v2 = None

    async def open_session(
        self,
        proxy_type: str | None,
        proxy_pool_id: str | None,
        proxy_geo: dict[str, str] | None = None,
        max_retries: int | None = None,
        prem_proxy_options: dict | None = None,
    ) -> ProxySession:
        if not proxy_type or proxy_type == "none":
            return DirectSession()

        # A proxy was explicitly requested. If the provider isn't configured we
        # must NOT silently fall back to direct egress: that would send the
        # request from the real server IP while the response still reports the
        # requested proxy_type. Fail closed — callers that want direct ask for
        # proxy_type="none".
        if self._client is None:
            raise ProxyConfigError(
                f"proxy_type={proxy_type!r} requested but no CyberYozh API key is configured"
            )

        if proxy_type == "prem_res_rotating":
            if self._client_v2 is None:
                raise ProxyConfigError(
                    "proxy_type='prem_res_rotating' requested but the CyberYozh "
                    "v2 rotating-proxy client is not configured"
                )
            provider = PremProxyProvider(
                client=self._client_v2,
                proxy_geo=proxy_geo,
                prem_opts=prem_proxy_options,
            )
            session = PremProxySession(provider=provider, max_retries=max_retries)
            return await session.init()

        provider = CyberYozhProxyProvider(
            client=self._client,
            geo=proxy_geo
        )

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw=str(proxy_type),
            proxy_pool_id=proxy_pool_id,
            max_retries=max_retries,
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

        # prem_res_rotating uses the v2 gateway and carries no legacy pool IDs.
        if proxy_type == "prem_res_rotating":
            return ProxyListResponse(
                proxy_type=proxy_type,
                category="prem_res_rotating",
                configured=True,
                items=[],
            )

        category = get_category_proxy(normalize_proxy_raw_type(proxy_type))

        try:
            proxies = await self._client.proxy_history(category=category, expired=False)
        except Exception as exc:
            log.exception("failed to fetch proxy_history for category=%s", category)
            raise CyberYozhAPIError(f"cyberyozh_api_error: {exc}") from exc

        access_filter = _ACCESS_FILTER.get(proxy_type)
        items = [
            ProxyItem(
                id=str(p.id),
                # `GET /api/v1/proxies/available` is anonymous, and this is
                # the vendor's `url` verbatim — the same field every other
                # caller here treats as credential-bearing and redacts before
                # it reaches a log. No proxy type returns userinfo in it today,
                # so this closes a latent leak rather than an active one: it is
                # an upstream response-shape change away from handing
                # `user:pass@host` to an unauthenticated caller. The host:port
                # the field exists for survives redaction.
                url=redact_url(p.url),
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
