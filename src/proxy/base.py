from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.proxy.models import ProxyConfig


@dataclass(frozen=True)
class ProxyFailure:
    status_code: int | None
    error: str | None


@dataclass(frozen=True)
class ResolvedProxy:
    lease: ProxyLease | None
    provider: "ProxyProvider | None" = None


@dataclass(frozen=True)
class ProxyLease:
    """
    The specific proxy that we use in the attempt.
    source_id is needed so that we can exclude proxies that have already been tried
    and (for mobile) change the IP of a specific proxy.
    """
    config: ProxyConfig
    source_id: str | None = None
    change_ip_links: list[str] | None = None


class ProxySession(Protocol):  # pylint: disable=too-few-public-methods
    """
    Session owns proxy-type specifics (attempts, normalization, rotate/change_ip).
    """

    def max_attempts(self) -> int: ...

    def current_proxy(self) -> ProxyConfig | None: ...

    async def on_failure(self, failure: ProxyFailure) -> bool:
        """
        Called after a request failure that looks proxy-related.
        Returns True if worker should retry (session updated its proxy state).
        Returns False if worker should stop retrying.
        """


class ProxyProvider(Protocol):
    async def acquire(self, proxy_type: str, proxy_pool_id: str | None = None) -> ProxyLease: ...

    async def rotate_next(
        self,
        *,
        proxy_type: str,
        proxy_pool_id: str | None,
        exclude_source_ids: set[str],
    ) -> ProxyLease:
        """Get next proxy not in exclude_source_ids."""

    async def change_ip(self, lease: ProxyLease) -> None:
        """For mobile/LTE: change IP from current lease."""
