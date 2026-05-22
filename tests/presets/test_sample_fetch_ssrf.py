from __future__ import annotations

import pytest

import src.presets.service as service_mod
from src.presets.service import _assert_public_url, _fetch_sample_html
from src.presets.exceptions import SampleFetchError


class TestAssertPublicUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/x",
            "http://localhost/x",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/internal",
            "http://192.168.1.1/router",
            "http://172.16.0.1/x",
            "http://100.64.0.1/cgnat",
            "http://[::1]/x",
            # IPv4-mapped IPv6 literals: on Python 3.12 ipaddress does NOT
            # delegate is_private/is_link_local to the embedded IPv4, so these
            # must be unwrapped explicitly or they bypass the guard.
            "http://[::ffff:169.254.169.254]/latest/meta-data/",
            "http://[::ffff:127.0.0.1]/x",
            "http://[::ffff:10.0.0.1]/internal",
            "file:///etc/passwd",
            "ftp://internal/x",
        ],
    )
    def test_rejects_unsafe(self, url, mocker):
        # localhost resolves to a loopback addr; getaddrinfo is real for that.
        with pytest.raises(SampleFetchError):
            _assert_public_url(url)

    def test_allows_public_ip(self):
        # 93.184.216.34 is example.com's well-known public IP literal.
        _assert_public_url("http://93.184.216.34/x")  # no raise

    def test_allows_public_hostname(self, mocker):
        mocker.patch.object(
            service_mod.socket,
            "getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 80))],
        )
        _assert_public_url("https://example.com/page")  # no raise

    def test_rejects_hostname_resolving_to_private(self, mocker):
        mocker.patch.object(
            service_mod.socket,
            "getaddrinfo",
            return_value=[(2, 1, 6, "", ("10.1.2.3", 80))],
        )
        with pytest.raises(SampleFetchError):
            _assert_public_url("https://sneaky.internal/x")


class TestFetchRevalidatesRedirects:
    @pytest.mark.asyncio
    async def test_redirect_to_internal_is_blocked(self, mocker):
        mocker.patch.object(
            service_mod.socket,
            "getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 80))],
        )

        class _Resp:
            def __init__(self, status, location=None, text=""):
                self.status_code = status
                self.headers = {"location": location} if location else {}
                self.text = text

            def raise_for_status(self):
                pass

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                # public URL redirects to a metadata endpoint
                return _Resp(302, location="http://169.254.169.254/")

        mocker.patch.object(service_mod.httpx, "AsyncClient", _Client)
        with pytest.raises(SampleFetchError):
            await _fetch_sample_html("https://example.com/start")

    @pytest.mark.asyncio
    async def test_happy_fetch_returns_text(self, mocker):
        mocker.patch.object(
            service_mod.socket,
            "getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 80))],
        )

        class _Resp:
            status_code = 200
            headers: dict = {}
            text = "<html>ok</html>"

            def raise_for_status(self):
                pass

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                return _Resp()

        mocker.patch.object(service_mod.httpx, "AsyncClient", _Client)
        out = await _fetch_sample_html("https://example.com/p")
        assert out == "<html>ok</html>"
