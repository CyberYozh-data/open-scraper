from __future__ import annotations

import pytest

from src.api import proxies as proxies_mod
from src.api.proxies import _proxy_config_to_url, resolve_proxy
from src.proxy.models import ProxyConfig


class TestProxyConfigToUrl:
    def test_injects_credentials(self):
        cfg = ProxyConfig(server="socks5://host.example:1080", username="u", password="p")
        assert _proxy_config_to_url(cfg) == "socks5://u:p@host.example:1080"

    def test_no_credentials_returns_server(self):
        cfg = ProxyConfig(server="socks5://host.example:1080")
        assert _proxy_config_to_url(cfg) == "socks5://host.example:1080"

    def test_credentials_are_url_encoded(self):
        cfg = ProxyConfig(server="socks5://h:1080", username="u@x", password="p:w/d")
        # special chars must be percent-encoded so the URL stays well-formed
        assert _proxy_config_to_url(cfg) == "socks5://u%40x:p%3Aw%2Fd@h:1080"


class _FakeSession:
    def __init__(self, cfg):
        self._cfg = cfg

    def current_proxy(self):
        return self._cfg


class TestResolveProxyEndpoint:
    @pytest.mark.asyncio
    async def test_none_returns_null(self, mocker):
        spy = mocker.patch.object(proxies_mod.proxy_resolver, "open_session")
        out = await resolve_proxy(proxy_type="none")
        assert out.proxy_url is None
        spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolves_socks_url(self, mocker):
        cfg = ProxyConfig(server="socks5://h:1080", username="u", password="p")
        mocker.patch.object(
            proxies_mod.proxy_resolver,
            "open_session",
            new=mocker.AsyncMock(return_value=_FakeSession(cfg)),
        )
        out = await resolve_proxy(proxy_type="res_rotating", country_code="US")
        assert out.proxy_url == "socks5://u:p@h:1080"

    @pytest.mark.asyncio
    async def test_geo_and_pool_passed_to_resolver(self, mocker):
        cfg = ProxyConfig(server="socks5://h:1080")
        spy = mocker.AsyncMock(return_value=_FakeSession(cfg))
        mocker.patch.object(proxies_mod.proxy_resolver, "open_session", new=spy)
        await resolve_proxy(
            proxy_type="res_rotating", proxy_pool_id="pool1",
            country_code="DE", region="Bavaria", city="Munich",
        )
        _, kwargs = spy.call_args
        assert kwargs["proxy_pool_id"] == "pool1"
        assert kwargs["proxy_geo"] == {"country_code": "DE", "region": "Bavaria", "city": "Munich"}

    @pytest.mark.asyncio
    async def test_pool_exhausted_runtimeerror_maps_to_502(self, mocker):
        from fastapi import HTTPException

        mocker.patch.object(
            proxies_mod.proxy_resolver,
            "open_session",
            new=mocker.AsyncMock(side_effect=RuntimeError("no_more_proxies: pool")),
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_proxy(proxy_type="res_rotating")
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_direct_session_returns_null(self, mocker):
        mocker.patch.object(
            proxies_mod.proxy_resolver,
            "open_session",
            new=mocker.AsyncMock(return_value=_FakeSession(None)),
        )
        out = await resolve_proxy(proxy_type="res_rotating")
        assert out.proxy_url is None
