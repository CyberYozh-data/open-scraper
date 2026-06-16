from __future__ import annotations

import pytest

from src import ssrf
from src.ssrf import SSRFError, _ip_is_public, host_is_public, safe_get


class _Resp:
    def __init__(self, status_code=200, headers=None, url="", is_redirect=False):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.is_redirect = is_redirect


class _Client:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, follow_redirects=False):
        self.calls.append(url)
        return self.routes[url]


class TestIpIsPublic:
    @pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "0.0.0.0"])
    def test_rejects_non_public(self, ip):
        assert _ip_is_public(ip) is False

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1"])
    def test_allows_public(self, ip):
        assert _ip_is_public(ip) is True


class TestHostIsPublic:
    @pytest.mark.asyncio
    async def test_private_resolution_blocked(self, mocker):
        mocker.patch.object(ssrf, "_resolve", mocker.AsyncMock(return_value=["10.0.0.1"]))
        assert await host_is_public("evil.internal") is False

    @pytest.mark.asyncio
    async def test_any_private_ip_blocks(self, mocker):
        # A host resolving to one public + one private IP must be blocked.
        mocker.patch.object(ssrf, "_resolve", mocker.AsyncMock(return_value=["8.8.8.8", "127.0.0.1"]))
        assert await host_is_public("rebind.test") is False

    @pytest.mark.asyncio
    async def test_public_resolution_allowed(self, mocker):
        mocker.patch.object(ssrf, "_resolve", mocker.AsyncMock(return_value=["93.184.216.34"]))
        assert await host_is_public("example.com") is True


class TestSafeGet:
    @pytest.mark.asyncio
    async def test_blocks_private_host(self, mocker):
        mocker.patch.object(ssrf, "host_is_public", mocker.AsyncMock(return_value=False))
        with pytest.raises(SSRFError):
            await safe_get(_Client({}), "http://169.254.169.254/")

    @pytest.mark.asyncio
    async def test_follows_public_redirect(self, mocker):
        mocker.patch.object(ssrf, "host_is_public", mocker.AsyncMock(return_value=True))
        client = _Client({
            "https://a.com/": _Resp(302, {"location": "https://b.com/x"}, "https://a.com/", is_redirect=True),
            "https://b.com/x": _Resp(200, {}, "https://b.com/x"),
        })
        resp = await safe_get(client, "https://a.com/")
        assert resp.status_code == 200
        assert client.calls == ["https://a.com/", "https://b.com/x"]

    @pytest.mark.asyncio
    async def test_blocks_redirect_to_private(self, mocker):
        # First hop public, redirect target private -> blocked on re-validation.
        async def fake_public(host):
            return host == "a.com"

        mocker.patch.object(ssrf, "host_is_public", side_effect=fake_public)
        client = _Client({
            "https://a.com/": _Resp(302, {"location": "http://169.254.169.254/"}, "https://a.com/", is_redirect=True),
        })
        with pytest.raises(SSRFError):
            await safe_get(client, "https://a.com/")
