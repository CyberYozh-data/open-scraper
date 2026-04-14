from __future__ import annotations

from src.proxy.base import ProxySession
from src.proxy.cyberyozh.client import CyberYozhClient
from src.proxy.cyberyozh.provider import CyberYozhProxyProvider
from src.proxy.cyberyozh.session import CyberYozhSession
from src.settings import settings


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


proxy_resolver = ProxyResolver()
