from __future__ import annotations

from dataclasses import dataclass

from src.proxy.base import ProxyFailure, ProxyLease, ProxySession
from src.proxy.cyberyozh.provider import CyberYozhProxyProvider


@dataclass
class CyberYozhSession(ProxySession):
    provider: CyberYozhProxyProvider
    proxy_type_raw: str
    proxy_pool_id: str | None

    lease: ProxyLease | None = None
    exclude_ids: set[str] | None = None

    async def init(self) -> "CyberYozhSession":
        self.exclude_ids = set()
        self.lease = await self.provider.acquire(
            proxy_type_raw=self.proxy_type_raw,
            proxy_pool_id=self.proxy_pool_id,
        )
        return self

    def max_attempts(self) -> int:
        return self.provider.max_attempts(self.proxy_type_raw)

    def current_proxy(self):
        return self.lease.config if self.lease else None

    async def on_failure(self, failure: ProxyFailure) -> bool:
        assert self.exclude_ids is not None

        self.lease, should_retry = await self.provider.recover(
            proxy_type_raw=self.proxy_type_raw,
            proxy_pool_id=self.proxy_pool_id,
            lease=self.lease,
            exclude_source_ids=self.exclude_ids,
            failure=failure,
        )
        return should_retry
