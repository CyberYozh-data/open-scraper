from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.proxy.base import ProxyConfigError, ProxyFailure, ProxyLease, ProxySession
from src.proxy.cyberyozh.client_v2 import CyberYozhV2Client
from src.proxy.cyberyozh.username import (
    UsernameParts,
    apply_session_suffix,
    assemble_username,
    gen_sticky_id,
    resolve_geo_parts,
    targeting_suffix,
)
from src.proxy.models import ProxyConfig
from src.settings import settings

log = logging.getLogger(__name__)


@dataclass
class PremProxyProvider:
    client: CyberYozhV2Client
    proxy_geo: dict[str, Any] | None
    prem_opts: dict[str, Any] | None
    # Per-session caches. The subscription endpoint, the selected sub-user, and
    # the resolved geo suffixes are invariant across retries of the same job, so
    # resolve them once and reuse on recover() instead of re-hitting the
    # CyberYozh control API every attempt. A provider's lifetime is one
    # open_session() == one job, so there's no cross-job leakage. The per-attempt
    # sticky/ttl token is NOT cached — it is reapplied each acquire().
    _endpoint: tuple[str, str, str] | None = field(default=None, init=False, repr=False)
    _user: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _geo_parts: UsernameParts | None = field(default=None, init=False, repr=False)

    def max_attempts(self, override: int | None = None) -> int:
        base = settings.max_retries if override is None else override
        return max(1, int(base))

    async def _select_sub_user(self) -> dict[str, Any]:
        users = await self.client.sub_users()
        if not users:
            raise RuntimeError("no v2 sub-users available")
        wanted = (self.prem_opts or {}).get("sub_user_id")
        if wanted:
            for u in users:
                if str(u.get("id")) == str(wanted):
                    return u
            log.warning("prem sub_user_id=%s not found, using first", wanted)
        return users[0]

    async def _resolve_endpoint(self) -> tuple[str, str, str]:
        """(scheme, host, port) from the subscription — fetched once per session."""
        if self._endpoint is None:
            sub = await self.client.subscription()
            if sub.get("provisioning_status") != "active":
                raise RuntimeError(
                    f"prem subscription not active: {sub.get('provisioning_status')!r}"
                )
            protocol = (self.prem_opts or {}).get("protocol", "http")
            if protocol == "socks5":
                port, scheme = sub.get("proxy_port_socks5"), "socks5"
            else:
                port, scheme = sub.get("proxy_port"), "http"
            host = sub.get("proxy_host")
            if not host or not port:
                raise RuntimeError("prem subscription missing host/port")
            self._endpoint = (scheme, str(host), str(port))
        return self._endpoint

    async def _resolve_user(self) -> dict[str, Any]:
        """The selected sub-user — fetched once per session, with required fields
        validated so a missing real_login/password surfaces as a clear
        RuntimeError (→ 502) rather than a bare KeyError (→ opaque 500)."""
        if self._user is None:
            user = await self._select_sub_user()
            if not user.get("real_login") or not user.get("password"):
                raise RuntimeError("prem sub_user missing real_login/password")
            self._user = user
        return self._user

    async def acquire(self) -> ProxyLease:
        scheme, host, port = await self._resolve_endpoint()
        user = await self._resolve_user()
        if self._geo_parts is None:
            self._geo_parts = await resolve_geo_parts(
                self.client,
                real_login=str(user["real_login"]),
                proxy_geo=self.proxy_geo,
                prem_opts=self.prem_opts,
            )
        # Reapply the per-attempt sticky/ttl token (recover() may have minted a
        # fresh sticky_id) to the cached, otherwise-static geo parts.
        parts = apply_session_suffix(self._geo_parts, self.prem_opts)

        return ProxyLease(
            config=ProxyConfig(
                server=f"{scheme}://{host}:{port}",
                username=assemble_username(parts),
                password=str(user["password"]),
                targeting_suffix=targeting_suffix(parts),
            ),
            source_id=str(user.get("id")),
        )

    async def recover(
        self, *, lease: ProxyLease | None, failure: ProxyFailure
    ) -> tuple[ProxyLease | None, bool]:
        # Sticky: rotate the exit by minting a fresh sticky id. Rotating: a fresh
        # acquire() over a new connection already yields a new exit IP.
        opts = self.prem_opts or {}
        if opts.get("session_type") == "sticky":
            opts = {**opts, "sticky_id": gen_sticky_id()}
            self.prem_opts = opts
        try:
            return await self.acquire(), True
        except ProxyConfigError:
            # Unsatisfiable request — must reach the worker's classification,
            # not degrade into a generic "recover failed".
            raise
        except Exception as exc:  # pylint: disable=broad-except
            log.error("prem recover failed: %s", exc)
            return lease, False


@dataclass
class PremProxySession(ProxySession):
    provider: PremProxyProvider
    max_retries: int | None = None
    lease: ProxyLease | None = None

    async def init(self) -> "PremProxySession":
        self.lease = await self.provider.acquire()
        return self

    def max_attempts(self) -> int:
        return self.provider.max_attempts(override=self.max_retries)

    def current_proxy(self):
        return self.lease.config if self.lease else None

    async def on_failure(self, failure: ProxyFailure) -> bool:
        self.lease, should_retry = await self.provider.recover(
            lease=self.lease, failure=failure
        )
        return should_retry
