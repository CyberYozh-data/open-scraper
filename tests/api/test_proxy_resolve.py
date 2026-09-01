from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.api import proxies as proxies_mod
from src.api.proxies import _proxy_config_to_url, resolve_proxy
from src.proxy.base import ProxyConfigError
from src.proxy.models import ProxyConfig
from src.settings import settings


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
    async def test_prem_options_passed_to_resolver(self, mocker):
        cfg = ProxyConfig(server="http://gate:10000", username="u", password="p")
        spy = mocker.AsyncMock(return_value=_FakeSession(cfg))
        mocker.patch.object(proxies_mod.proxy_resolver, "open_session", new=spy)
        await resolve_proxy(
            proxy_type="prem_res_rotating", country_code="RU",
            ip_filter="quality-security", isp="ArtTelecom",
            session_type="sticky", rotation_minutes=10, sub_user_id="su1",
        )
        _, kwargs = spy.call_args
        assert kwargs["proxy_geo"] == {"country_code": "RU"}
        assert kwargs["prem_proxy_options"] == {
            "ip_filter": "quality-security", "isp": "ArtTelecom",
            "session_type": "sticky", "rotation_minutes": 10, "sub_user_id": "su1",
        }

    @pytest.mark.asyncio
    async def test_prem_options_omitted_when_unset(self, mocker):
        cfg = ProxyConfig(server="http://gate:10000")
        spy = mocker.AsyncMock(return_value=_FakeSession(cfg))
        mocker.patch.object(proxies_mod.proxy_resolver, "open_session", new=spy)
        await resolve_proxy(proxy_type="res_rotating", country_code="US")
        _, kwargs = spy.call_args
        assert kwargs["prem_proxy_options"] is None

    @pytest.mark.asyncio
    async def test_invalid_prem_options_return_422(self, mocker):
        # The /resolve helper must run its flat query params through the same
        # PremProxyOptions validation /scrape uses — bad values are rejected
        # before reaching the resolver, not silently passed upstream.
        from fastapi import HTTPException

        spy = mocker.patch.object(proxies_mod.proxy_resolver, "open_session")
        for kwargs in (
            {"ip_filter": "bogus"},
            {"session_type": "sticky", "rotation_minutes": -5},
            {"session_type": "sticky", "sticky_id": "x-filter-iqs"},
        ):
            with pytest.raises(HTTPException) as exc:
                await resolve_proxy(proxy_type="prem_res_rotating", **kwargs)
            assert exc.value.status_code == 422
        spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_country_code_returns_422(self, mocker):
        from fastapi import HTTPException

        spy = mocker.patch.object(proxies_mod.proxy_resolver, "open_session")
        with pytest.raises(HTTPException) as exc:
            await resolve_proxy(
                proxy_type="prem_res_rotating", country_code="ru-r-77-ct-moscow"
            )
        assert exc.value.status_code == 422
        spy.assert_not_called()

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
    async def test_unsatisfiable_geo_maps_to_422(self, mocker):
        # Well-formed params the catalog can't satisfy (e.g. a region name from
        # another country) are user input like the validation failures above —
        # 422, not an opaque 500.
        from fastapi import HTTPException

        mocker.patch.object(
            proxies_mod.proxy_resolver,
            "open_session",
            new=mocker.AsyncMock(
                side_effect=ProxyConfigError("no v2 geo suffix for name='Dagestan'")
            ),
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_proxy(
                proxy_type="prem_res_rotating", country_code="CA", region="Dagestan"
            )
        assert exc.value.status_code == 422
        assert "Dagestan" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_direct_session_returns_null(self, mocker):
        mocker.patch.object(
            proxies_mod.proxy_resolver,
            "open_session",
            new=mocker.AsyncMock(return_value=_FakeSession(None)),
        )
        out = await resolve_proxy(proxy_type="res_rotating")
        assert out.proxy_url is None


class TestResolveProxyServiceAuth:
    """CRIT-01: GET /proxies/resolve returns a URL with embedded, reusable proxy
    credentials, so it must never be reachable without the shared SERVICE_TOKEN.
    Fail-closed: with no token configured it refuses to serve credentials at all.

    These exercise the FastAPI dependency over the HTTP stack (the unit tests
    above call the endpoint function directly and bypass route dependencies).
    """

    @staticmethod
    def _client() -> TestClient:
        from src.app import create_app

        # No lifespan (`with`) — the auth gate needs neither Redis nor the job
        # store, and skipping startup keeps the test hermetic.
        return TestClient(create_app())

    @pytest.mark.parametrize(
        "token, header, status",
        [
            (None, None, 503),  # unset -> fail closed, refuse to serve
            (SecretStr(""), "", 503),  # empty is "unconfigured" too -> fail closed
            (SecretStr("s3cret"), None, 401),  # configured, header missing
            (SecretStr("s3cret"), "wrong", 401),  # configured, wrong header
        ],
    )
    def test_rejects_without_valid_token(self, monkeypatch, mocker, token, header, status):
        monkeypatch.setattr(settings, "service_token", token)
        spy = mocker.patch.object(proxies_mod.proxy_resolver, "open_session")
        headers = {} if header is None else {"X-Service-Token": header}
        resp = self._client().get(
            "/api/v1/proxies/resolve",
            params={"proxy_type": "res_rotating"},
            headers=headers,
        )
        assert resp.status_code == status
        spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_ascii_header_is_rejected_not_500(self, monkeypatch):
        # ASGI servers decode raw header bytes as latin-1, so the token string
        # reaching the guard can hold code points 128-255. hmac.compare_digest
        # raises TypeError on a str with non-ASCII — the guard must encode first
        # and return a clean 401, not a 500. (The httpx TestClient refuses to
        # send non-ASCII headers, so this exercises the dependency directly.)
        from fastapi import HTTPException

        from src.api.service_auth import require_service_token

        monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
        # The guard takes the Request it logs the path and peer host from;
        # this drives it directly, so build a minimal one.
        from starlette.requests import Request as _Request

        request = _Request(
            {"type": "http", "method": "GET", "path": "/api/v1/proxies/resolve",
             "headers": [], "client": ("127.0.0.1", 1234), "query_string": b""}
        )
        with pytest.raises(HTTPException) as exc:
            await require_service_token(request, x_service_token="\xff\xfe")
        assert exc.value.status_code == 401

    def test_none_proxy_type_still_requires_token(self, monkeypatch):
        # proxy_type=none short-circuits inside the handler, but the gate runs
        # first — don't even confirm the endpoint shape to an anonymous caller.
        monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
        resp = self._client().get(
            "/api/v1/proxies/resolve", params={"proxy_type": "none"}
        )
        assert resp.status_code == 401

    def test_correct_header_allows(self, monkeypatch, mocker):
        monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
        cfg = ProxyConfig(server="socks5://h:1080", username="u", password="p")
        mocker.patch.object(
            proxies_mod.proxy_resolver,
            "open_session",
            new=mocker.AsyncMock(return_value=_FakeSession(cfg)),
        )
        resp = self._client().get(
            "/api/v1/proxies/resolve",
            params={"proxy_type": "res_rotating", "country_code": "US"},
            headers={"X-Service-Token": "s3cret"},
        )
        assert resp.status_code == 200
        assert resp.json()["proxy_url"] == "socks5://u:p@h:1080"
