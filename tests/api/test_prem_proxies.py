from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.api import prem_proxies
from src.app import create_app
from src.settings import settings

# The router is gated now, so every pre-existing test speaks as an authorised
# caller. The gate itself is exercised by TestEveryRouteIsGated below, which
# builds its own unauthenticated clients.
_TOKEN = "test-service-token"
_AUTH = {"X-Service-Token": _TOKEN}


@pytest.fixture(autouse=True)
def _service_token(monkeypatch):
    monkeypatch.setattr(settings, "service_token", SecretStr(_TOKEN))


@pytest.fixture(autouse=True)
def _fresh_relay_state():
    """The cache and the upstream budget are module-level on purpose — they
    have to be shared across requests to be a defence. That makes them shared
    across tests too, so reset around each one."""
    prem_proxies.reset_relay_state()
    yield
    prem_proxies.reset_relay_state()


def test_sub_users_strips_secrets():
    client_v2 = AsyncMock()
    client_v2.sub_users.return_value = [
        {"id": "1", "login": "Giterfull", "real_login": "Giterfull897e009c",
         "password": "SECRET", "is_primary": True, "traffic_left_mb": 5000},
    ]
    with patch("src.api.prem_proxies.proxy_resolver") as pr:
        pr._client_v2 = client_v2
        with TestClient(create_app(), headers=_AUTH) as tc:
            resp = tc.get("/api/v2/prem-proxies/sub-users")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"id": "1", "login": "Giterfull", "is_primary": True}]
    assert "SECRET" not in resp.text and "real_login" not in resp.text


def test_subscription_omits_when_unconfigured():
    with patch("src.api.prem_proxies.proxy_resolver") as pr:
        pr._client_v2 = None
        with TestClient(create_app(), headers=_AUTH) as tc:
            resp = tc.get("/api/v2/prem-proxies/sub-users")
    assert resp.status_code == 200
    assert resp.json() == []


def test_session_options_allowlists_keys():
    """session-options only returns the keys the UI needs; extra keys are dropped."""
    client_v2 = AsyncMock()
    client_v2.session_options.return_value = {
        "ip_filters": ["max-size-security"],
        "session_durations": [5, 10],
        "protocols": ["http"],
        "username_grammar": "{user}:{pass}",
        "internal_secret": "should_be_dropped",
        "other_field": 42,
    }
    with patch("src.api.prem_proxies.proxy_resolver") as pr:
        pr._client_v2 = client_v2
        with TestClient(create_app(), headers=_AUTH) as tc:
            resp = tc.get("/api/v2/prem-proxies/session-options")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"ip_filters", "session_durations", "protocols", "username_grammar"}
    assert "internal_secret" not in body
    assert "other_field" not in body


def test_upstream_error_yields_graceful_empty():
    """An upstream exception from client_v2 returns the graceful empty shape."""
    client_v2 = AsyncMock()
    client_v2.sub_users.side_effect = RuntimeError("upstream down")
    client_v2.subscription.side_effect = RuntimeError("upstream down")
    client_v2.session_options.side_effect = RuntimeError("upstream down")

    with patch("src.api.prem_proxies.proxy_resolver") as pr:
        pr._client_v2 = client_v2
        with TestClient(create_app(), headers=_AUTH) as tc:
            r_users = tc.get("/api/v2/prem-proxies/sub-users")
            r_sub = tc.get("/api/v2/prem-proxies/subscription")
            r_opts = tc.get("/api/v2/prem-proxies/session-options")

    assert r_users.status_code == 200
    assert r_users.json() == []

    assert r_sub.status_code == 200
    assert r_sub.json() == {"configured": False}

    assert r_opts.status_code == 200
    assert r_opts.json() == {}


class TestUpstreamRelayIsBounded:
    """Every `geo/*` hit opened a fresh httpx client and drove the account's
    CYBERYOZH_API_KEY at the vendor, uncached and unlimited. Anonymous or not,
    that is an open relay burning a paid quota — and unlike the disclosure
    half, closing it needs no coordination with any consumer.
    """

    @staticmethod
    def _client_with(**returns):
        client_v2 = AsyncMock()
        for name, value in returns.items():
            getattr(client_v2, name).return_value = value
        return client_v2

    def test_repeated_identical_geo_requests_hit_upstream_once(self):
        client_v2 = self._client_with(geo_countries=[{"code": "US"}])
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = client_v2
            with TestClient(create_app(), headers=_AUTH) as tc:
                first = tc.get("/api/v2/prem-proxies/geo/countries")
                second = tc.get("/api/v2/prem-proxies/geo/countries")
        assert first.json() == second.json() == [{"code": "US"}]
        assert client_v2.geo_countries.await_count == 1

    def test_cache_key_includes_the_parameters(self):
        """An over-eager cache serving one country's cities for another is a
        correctness bug dressed as a fix."""
        client_v2 = AsyncMock()
        client_v2.geo_cities.side_effect = [[{"name": "Berlin"}], [{"name": "Paris"}]]
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = client_v2
            with TestClient(create_app(), headers=_AUTH) as tc:
                de = tc.get("/api/v2/prem-proxies/geo/cities?country_code=DE")
                fr = tc.get("/api/v2/prem-proxies/geo/cities?country_code=FR")
        assert de.json() == [{"name": "Berlin"}]
        assert fr.json() == [{"name": "Paris"}]
        assert client_v2.geo_cities.await_count == 2

    def test_exhausted_budget_returns_429_without_calling_upstream(self):
        """429, never an empty list: `[]` is indistinguishable from 'this
        country has no ISPs' and the UI would render a silent lie."""
        client_v2 = AsyncMock()
        client_v2.geo_cities.side_effect = lambda *a, **k: [{"name": "x"}]
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = client_v2
            with TestClient(create_app(), headers=_AUTH) as tc:
                # Distinct keys, so the cache cannot absorb them.
                codes = [
                    f"{chr(65 + i // 26)}{chr(65 + i % 26)}"
                    for i in range(prem_proxies.UPSTREAM_BUDGET)
                ]
                for code in codes:
                    resp = tc.get(f"/api/v2/prem-proxies/geo/cities?country_code={code}")
                    assert resp.status_code == 200, resp.text
                spent = client_v2.geo_cities.await_count
                blocked = tc.get("/api/v2/prem-proxies/geo/cities?country_code=ZZ")
        assert blocked.status_code == 429
        assert blocked.json() != []
        assert client_v2.geo_cities.await_count == spent

    @pytest.mark.parametrize(
        "query",
        [
            "country_code=../../etc/passwd",
            "country_code=" + "A" * 500,
            "country_code=",
            "country_code=DE&region_code=" + "x" * 500,
        ],
    )
    def test_malformed_parameters_are_rejected_before_upstream(self, query):
        client_v2 = self._client_with(geo_cities=[])
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = client_v2
            with TestClient(create_app(), headers=_AUTH) as tc:
                resp = tc.get(f"/api/v2/prem-proxies/geo/cities?{query}")
        assert resp.status_code == 422, resp.text
        assert client_v2.geo_cities.await_count == 0


class TestTheBoundsAreActuallyBounded:
    """The first draft bounded the RATE of upstream calls and nothing else."""

    @pytest.mark.asyncio
    async def test_the_cache_cannot_grow_without_limit(self):
        """`max_length=64` bounds each key's SIZE, not the COUNT. `city_name`
        is free-form, so a caller cycling values leaves one permanent entry per
        request — each holding a full vendor response."""
        from src.api import prem_proxies

        client_v2 = AsyncMock()
        client_v2.geo_zips.return_value = [{"zip": "10115"}]
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = client_v2
            for i in range(prem_proxies.CACHE_MAX + 40):
                # The budget is not what is under test here; keep it full so
                # the loop reaches the cache bound instead of a 429.
                prem_proxies._tokens = float(prem_proxies.UPSTREAM_BUDGET)
                await prem_proxies.geo_zips(country_code="DE", city_name=f"c{i}")
        assert len(prem_proxies._cache) <= prem_proxies.CACHE_MAX

    @pytest.mark.asyncio
    async def test_a_stale_entry_is_not_left_behind(self, monkeypatch):
        from src.api import prem_proxies

        monkeypatch.setattr(prem_proxies, "_GEO_TTL_S", 0.0)
        client_v2 = AsyncMock()
        client_v2.geo_zips.return_value = []
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = client_v2
            for _ in range(5):
                await prem_proxies.geo_zips(country_code="DE", city_name="x")
        assert len(prem_proxies._cache) == 1

    @pytest.mark.asyncio
    async def test_country_code_case_does_not_multiply_the_key_space(self):
        """`US`, `us` and `Us` are one country. Three keys meant three upstream
        calls and three cache entries for the same answer."""
        from src.api import prem_proxies

        client_v2 = AsyncMock()
        client_v2.geo_regions.return_value = [{"code": "BY"}]
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = client_v2
            for code in ("DE", "de", "De"):
                await prem_proxies.geo_regions(country_code=code)
        assert client_v2.geo_regions.await_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_misses_make_one_upstream_call(self):
        """The cache collapses SEQUENTIAL traffic only. Under a burst — which
        is what the admin panel's cascading dropdowns produce — every caller
        missed and every caller spent a token."""
        import asyncio

        from src.api import prem_proxies

        client_v2 = AsyncMock()

        async def _slow(*_a, **_k):
            await asyncio.sleep(0.05)
            return [{"code": "BE"}]

        client_v2.geo_regions.side_effect = _slow
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = client_v2
            await asyncio.gather(
                *(prem_proxies.geo_regions(country_code="DE") for _ in range(20))
            )
        assert client_v2.geo_regions.await_count == 1

    @pytest.mark.asyncio
    async def test_the_budget_cannot_be_doubled_by_straddling(self, monkeypatch):
        """A FIXED window resets at a boundary: spend it just before and just
        after and a caller gets 2x within a fraction of a second of REAL time.
        A bucket refills continuously, so 0.2s buys 0.2 tokens.

        (A full budget again after a full interval is correct and not what this
        pins — that IS the configured rate.)
        """
        from src.api import prem_proxies

        clock = {"t": 1000.0}
        monkeypatch.setattr(prem_proxies.time, "monotonic", lambda: clock["t"])
        client_v2 = AsyncMock()
        client_v2.geo_regions.return_value = []
        granted = 0
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = client_v2
            for i in range(prem_proxies.UPSTREAM_BUDGET):
                await prem_proxies.geo_regions(country_code=_code(i))
                granted += 1
            # Two tenths of a second later — the straddle a fixed window allows.
            clock["t"] = 1000.2
            for i in range(prem_proxies.UPSTREAM_BUDGET):
                try:
                    await prem_proxies.geo_regions(country_code=_code(1000 + i))
                    granted += 1
                except HTTPException:
                    break
        assert granted <= prem_proxies.UPSTREAM_BUDGET + 1, granted


def _code(i: int) -> str:
    return f"{chr(65 + (i // 26) % 26)}{chr(65 + i % 26)}"
class TestEveryRouteIsGated:
    """Derived from the router, not from a hand-written list.

    The defect this closes is exactly a rule that could not name what it was
    supposed to cover: `mcp_excluded_operations()` keyed on `operation_id` and
    these routes declared none. Parametrising over `router.routes` means a
    ninth endpoint added to this file is covered the moment it exists.
    """

    # Only GET routes: the gate applies to the router, but the probes below
    # send GETs, and a future POST here would fail all three with 405 while
    # pointing at the wrong thing.
    ROUTES: ClassVar[list[str]] = [
        r.path for r in prem_proxies.router.routes if "GET" in getattr(r, "methods", set())
    ]

    def test_the_router_actually_has_routes(self):
        """Guards the guard: a parametrisation over an empty list is a suite
        that passes by testing nothing. Asserted as non-empty rather than
        pinned to 8 — a ninth endpoint should be COVERED by this class, not
        blocked by it."""
        assert self.ROUTES
        assert all(p.startswith("/api/v2/prem-proxies") for p in self.ROUTES)

    @pytest.mark.parametrize("path", ROUTES)
    def test_a_route_refuses_an_anonymous_caller(self, path, monkeypatch):
        monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = AsyncMock()
            with TestClient(create_app()) as tc:
                resp = tc.get(path, params={"country_code": "DE"})
        assert resp.status_code == 401, f"{path} answered {resp.status_code}"

    @pytest.mark.parametrize("path", ROUTES)
    def test_a_route_fails_closed_when_no_token_is_configured(self, path, monkeypatch):
        """503, not an open endpoint: refusing to serve beats serving paid
        account metadata because someone forgot to set the secret."""
        monkeypatch.setattr(settings, "service_token", None)
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = AsyncMock()
            with TestClient(create_app()) as tc:
                resp = tc.get(path, params={"country_code": "DE"})
        assert resp.status_code == 503, f"{path} answered {resp.status_code}"

    @pytest.mark.parametrize("path", ROUTES)
    def test_a_route_accepts_the_right_token(self, path, monkeypatch):
        monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
        with patch("src.api.prem_proxies.proxy_resolver") as pr:
            pr._client_v2 = None
            with TestClient(create_app()) as tc:
                resp = tc.get(
                    path,
                    params={"country_code": "DE"},
                    headers={"X-Service-Token": "s3cret"},
                )
        # NOT `== 200`: a future route with a different required parameter
        # would answer 422 and report as "the gate rejected a valid token",
        # and the natural fix for that is to relax the assertion — the wrong
        # direction. What this test owns is that the gate does not refuse.
        assert resp.status_code not in (401, 403, 503), (
            f"{path} answered {resp.status_code} for a valid token"
        )

    def test_every_route_declares_a_stable_operation_id(self):
        """FastAPI-generated ids are path-derived, so renaming a handler
        silently renames its tool. Explicit ids make these routes nameable by
        any future rule — which is what the old exclusion could not do."""
        ids = [
            getattr(r, "operation_id", None) for r in prem_proxies.router.routes
        ]
        assert all(ids), f"routes without an explicit operation_id: {ids}"
        assert all(i.startswith("prem_") for i in ids), ids
