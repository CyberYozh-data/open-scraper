from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.proxy.cyberyozh.provider_v2 import PremProxyProvider, PremProxySession
from src.proxy.base import ProxyFailure


def _client():
    c = AsyncMock()
    c.subscription.return_value = {
        "proxy_host": "gate.cyberyozh.net",
        "proxy_port": 10000,
        "proxy_port_socks5": 11000,
        "provisioning_status": "active",
    }
    c.sub_users.return_value = [
        {"id": "1", "login": "Giterfull", "real_login": "Giterfull897e009c",
         "password": "secret", "is_primary": True},
    ]
    return c


@pytest.mark.asyncio
async def test_acquire_builds_proxy_config():
    provider = PremProxyProvider(
        client=_client(),
        proxy_geo={"country_code": "RU"},
        prem_opts={"ip_filter": "quality-security", "protocol": "http"},
    )
    lease = await provider.acquire()
    assert lease.config.server == "http://gate.cyberyozh.net:10000"
    assert lease.config.username == "Giterfull897e009c-c-ru-filter-iqs"
    assert lease.config.password == "secret"
    # Targeting echoed at the source — the suffix only, never the account login.
    assert lease.config.targeting_suffix == "c-ru-filter-iqs"
    assert lease.source_id == "1"


@pytest.mark.asyncio
async def test_acquire_socks5_uses_socks_port_and_scheme():
    provider = PremProxyProvider(
        client=_client(), proxy_geo={"country_code": "RU"},
        prem_opts={"protocol": "socks5"},
    )
    lease = await provider.acquire()
    assert lease.config.server == "socks5://gate.cyberyozh.net:11000"


@pytest.mark.asyncio
async def test_acquire_selects_sub_user_by_id():
    c = _client()
    c.sub_users.return_value = [
        {"id": "1", "login": "a", "real_login": "a_rl", "password": "p1", "is_primary": True},
        {"id": "2", "login": "b", "real_login": "b_rl", "password": "p2", "is_primary": False},
    ]
    provider = PremProxyProvider(client=c, proxy_geo={"country_code": "RU"},
                                 prem_opts={"sub_user_id": "2"})
    lease = await provider.acquire()
    assert lease.config.password == "p2"
    assert lease.config.username.startswith("b_rl")


@pytest.mark.asyncio
async def test_inactive_subscription_raises():
    c = _client()
    c.subscription.return_value = {"proxy_host": "h", "proxy_port": 1, "provisioning_status": "pending"}
    provider = PremProxyProvider(client=c, proxy_geo=None, prem_opts=None)
    with pytest.raises(RuntimeError):
        await provider.acquire()


@pytest.mark.asyncio
async def test_session_recover_rotates_and_retries():
    provider = PremProxyProvider(client=_client(), proxy_geo={"country_code": "RU"}, prem_opts={})
    session = await PremProxySession(provider=provider, max_retries=3).init()
    assert session.max_attempts() == 3
    ok = await session.on_failure(ProxyFailure(status_code=403, error="blocked"))
    assert ok is True
    assert session.current_proxy() is not None


@pytest.mark.asyncio
async def test_sub_user_missing_fields_raises_runtimeerror():
    # An upstream /sub-users/ object missing real_login/password must raise a
    # clear RuntimeError (-> 502 on /resolve), not a bare KeyError (-> opaque 500).
    c = _client()
    c.sub_users.return_value = [{"id": "1", "login": "x"}]
    provider = PremProxyProvider(client=c, proxy_geo=None, prem_opts=None)
    with pytest.raises(RuntimeError):
        await provider.acquire()


@pytest.mark.asyncio
async def test_retry_reuses_cached_upstream_lookups():
    # Per-session caching: subscription + sub-users + geo suffixes are invariant
    # across retries and must be fetched ONCE, while the sticky token still
    # rotates per attempt (fresh exit IP).
    c = _client()
    c.geo_cities.return_value = [{"name": "Moscow", "suffix": "ct-msk"}]
    provider = PremProxyProvider(
        client=c,
        proxy_geo={"country_code": "RU", "city": "Moscow"},
        prem_opts={"session_type": "sticky", "sticky_id": "first"},
    )
    session = await PremProxySession(provider=provider, max_retries=3).init()
    u1 = session.current_proxy().username
    await session.on_failure(ProxyFailure(status_code=403, error="blocked"))
    u2 = session.current_proxy().username

    assert c.subscription.await_count == 1
    assert c.sub_users.await_count == 1
    assert c.geo_cities.await_count == 1
    # geo targeting identical and reused; only the sticky session token rotates
    assert "ct-msk" in u1 and "ct-msk" in u2
    assert u1 != u2
