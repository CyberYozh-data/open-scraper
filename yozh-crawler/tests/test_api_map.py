from __future__ import annotations

import pytest

from src.api.map import build_map
from src.schemas import CrawlScope, MapRequest


@pytest.fixture(autouse=True)
def _allow_all_hosts(mocker):
    # Bypass the SSRF DNS check so unit tests don't hit the network.
    mocker.patch("src.ssrf.host_is_public", mocker.AsyncMock(return_value=True))


class _Resp:
    def __init__(self, status_code, text="", url="", is_redirect=False, headers=None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.is_redirect = is_redirect
        self.headers = headers or {}


class _StubClient:
    def __init__(self, routes):
        self.routes = routes

    async def get(self, url, follow_redirects=False):
        resp = self.routes.get(url, _Resp(404))
        if not resp.url:
            resp.url = url
        return resp


_SITEMAP = (
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://x.com/a</loc></url>"
    "<url><loc>https://x.com/a</loc></url>"  # duplicate -> deduped
    "<url><loc>https://y.com/evil</loc></url>"  # other domain -> out of scope
    "</urlset>"
)
_SEED_HTML = '<html><body><a href="/p1">1</a><a href="https://x.com/p2">2</a></body></html>'


def _req(**kw):
    return MapRequest(seed_url="https://x.com/", **kw)


class TestBuildMap:
    @pytest.mark.asyncio
    async def test_sitemap_discovery_scope_and_dedup(self):
        client = _StubClient({"https://x.com/sitemap.xml": _Resp(200, _SITEMAP)})
        res = await build_map(
            _req(include_page_links=False), http_client=client, scraper_fetch=None
        )
        # seed + /a ; /a dedup'd ; y.com dropped by same-domain scope
        assert res.urls == ["https://x.com/", "https://x.com/a"]
        assert res.stats.from_sitemap == 3
        assert res.stats.unique_in_scope == 2

    @pytest.mark.asyncio
    async def test_page_links_via_httpx(self):
        client = _StubClient({"https://x.com/": _Resp(200, _SEED_HTML)})
        res = await build_map(
            _req(include_sitemap=False), http_client=client, scraper_fetch=None
        )
        assert "https://x.com/p1" in res.urls
        assert "https://x.com/p2" in res.urls
        assert res.stats.from_page == 2

    @pytest.mark.asyncio
    async def test_render_uses_scraper(self):
        calls = []

        async def fake_fetch(url, opts):
            calls.append((url, opts))
            return {"raw_html": _SEED_HTML}

        client = _StubClient({})
        res = await build_map(
            _req(include_sitemap=False, render=True),
            http_client=client,
            scraper_fetch=fake_fetch,
        )
        assert calls and calls[0][1].get("render") is True
        assert "https://x.com/p1" in res.urls

    @pytest.mark.asyncio
    async def test_search_filter(self):
        client = _StubClient({"https://x.com/": _Resp(200, _SEED_HTML)})
        res = await build_map(
            _req(include_sitemap=False, search="p2"),
            http_client=client,
            scraper_fetch=None,
        )
        assert res.urls == ["https://x.com/p2"]

    @pytest.mark.asyncio
    async def test_limit_caps_results(self):
        client = _StubClient({"https://x.com/": _Resp(200, _SEED_HTML)})
        res = await build_map(
            _req(include_sitemap=False, limit=1),
            http_client=client,
            scraper_fetch=None,
        )
        assert len(res.urls) == 1
        assert res.count == 1

    @pytest.mark.asyncio
    async def test_seed_fetch_failure_is_warning_not_error(self):
        client = _StubClient({"https://x.com/": _Resp(500)})
        res = await build_map(
            _req(include_sitemap=False), http_client=client, scraper_fetch=None
        )
        assert res.urls == ["https://x.com/"]  # only the seed
        assert any("seed fetch returned 500" in w for w in res.warnings)

    @pytest.mark.asyncio
    async def test_output_is_canonicalized(self):
        # Fragment dropped, default port stripped, duplicate variants collapsed —
        # so /map returns the same URL shape as /crawl.
        sitemap = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://x.com:443/a#frag</loc></url>"
            "<url><loc>https://x.com/a</loc></url>"
            "</urlset>"
        )
        client = _StubClient({"https://x.com/sitemap.xml": _Resp(200, sitemap)})
        res = await build_map(
            _req(include_page_links=False), http_client=client, scraper_fetch=None
        )
        assert res.urls == ["https://x.com/", "https://x.com/a"]

    @pytest.mark.asyncio
    async def test_ssrf_private_seed_is_blocked_with_warning(self, mocker):
        # Override the autouse allow-all: a private/internal host must be refused.
        mocker.patch("src.ssrf.host_is_public", mocker.AsyncMock(return_value=False))
        client = _StubClient({})
        res = await build_map(
            MapRequest(seed_url="http://169.254.169.254/", include_sitemap=False),
            http_client=client,
            scraper_fetch=None,
        )
        assert res.urls == ["http://169.254.169.254/"]  # seed echoed, never fetched
        assert any("seed fetch failed" in w for w in res.warnings)

    @pytest.mark.asyncio
    async def test_page_links_resolve_against_post_redirect_base(self):
        # Seed redirects to /new/; relative links must resolve against the final URL.
        client = _StubClient({
            "https://x.com/": _Resp(
                200, '<a href="rel">r</a>', url="https://x.com/new/"
            ),
        })
        res = await build_map(
            _req(include_sitemap=False), http_client=client, scraper_fetch=None
        )
        assert "https://x.com/new/rel" in res.urls

    @pytest.mark.asyncio
    async def test_regex_scope_filters(self):
        client = _StubClient({"https://x.com/": _Resp(200, _SEED_HTML)})
        scope = CrawlScope(mode="regex", include_patterns=[r"/p2$"])
        res = await build_map(
            _req(include_sitemap=False, scope=scope),
            http_client=client,
            scraper_fetch=None,
        )
        assert res.urls == ["https://x.com/p2"]


class TestMapTiming:
    @pytest.mark.asyncio
    async def test_response_includes_took_ms(self):
        client = _StubClient({"https://x.com/sitemap.xml": _Resp(200, _SITEMAP)})
        res = await build_map(
            _req(include_page_links=False), http_client=client, scraper_fetch=None
        )
        assert isinstance(res.took_ms, int)
        assert res.took_ms >= 0


class TestMapProxy:
    @pytest.mark.asyncio
    async def test_request_accepts_proxy_fields(self):
        req = MapRequest(
            seed_url="https://x.com/",
            proxy_type="res_rotating",
            proxy_pool_id="pool1",
            proxy_geo={"country_code": "US"},
        )
        assert req.proxy_type == "res_rotating"
        assert req.proxy_pool_id == "pool1"
        assert req.proxy_geo.country_code == "US"

    @pytest.mark.asyncio
    async def test_proxy_type_defaults_none(self):
        assert MapRequest(seed_url="https://x.com/").proxy_type == "none"

    @pytest.mark.asyncio
    async def test_render_forwards_proxy_to_scraper(self):
        calls = []

        async def fake_fetch(url, opts):
            calls.append(opts)
            return {"raw_html": _SEED_HTML}

        req = MapRequest(
            seed_url="https://x.com/", include_sitemap=False, render=True,
            proxy_type="res_rotating", proxy_pool_id="pool1",
            proxy_geo={"country_code": "US"},
        )
        await build_map(req, http_client=_StubClient({}), scraper_fetch=fake_fetch)
        assert calls and calls[0]["proxy_type"] == "res_rotating"
        assert calls[0]["proxy_pool_id"] == "pool1"
        assert calls[0]["proxy_geo"] == {"country_code": "US", "region": None, "city": None}

    @pytest.mark.asyncio
    async def test_check_ssrf_false_skips_host_block(self, mocker):
        # When proxied (check_ssrf=False) a private host must NOT be blocked —
        # egress goes through the proxy, the crawler isn't the SSRF vector.
        mocker.patch("src.ssrf.host_is_public", mocker.AsyncMock(return_value=False))
        client = _StubClient({"https://x.com/": _Resp(200, _SEED_HTML)})
        res = await build_map(
            _req(include_sitemap=False), http_client=client,
            scraper_fetch=None, check_ssrf=False,
        )
        assert "https://x.com/p1" in res.urls  # fetched despite private verdict

    @pytest.mark.asyncio
    async def test_extra_warnings_surfaced(self):
        res = await build_map(
            _req(include_sitemap=False, include_page_links=False),
            http_client=_StubClient({}), scraper_fetch=None,
            extra_warnings=["proxy resolve failed; used direct"],
        )
        assert any("proxy resolve failed" in w for w in res.warnings)
