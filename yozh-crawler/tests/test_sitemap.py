from __future__ import annotations

import pytest

from src.sitemap import (
    collect_sitemap_urls,
    discover_sitemap_urls,
    parse_robots_sitemaps,
    parse_sitemap_xml,
)


class TestParseSitemapXml:
    def test_urlset_returns_pages(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://x.com/a</loc></url>"
            "<url><loc>https://x.com/b</loc></url>"
            "</urlset>"
        )
        pages, sitemaps = parse_sitemap_xml(xml)
        assert pages == ["https://x.com/a", "https://x.com/b"]
        assert sitemaps == []

    def test_sitemapindex_returns_child_sitemaps(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://x.com/sitemap1.xml</loc></sitemap>"
            "<sitemap><loc>https://x.com/sitemap2.xml</loc></sitemap>"
            "</sitemapindex>"
        )
        pages, sitemaps = parse_sitemap_xml(xml)
        assert pages == []
        assert sitemaps == ["https://x.com/sitemap1.xml", "https://x.com/sitemap2.xml"]

    def test_malformed_returns_empty(self):
        pages, sitemaps = parse_sitemap_xml("not xml <<<")
        assert pages == [] and sitemaps == []

    def test_blank_locs_skipped(self):
        xml = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc></loc></url><url><loc>https://x.com/a</loc></url></urlset>"
        )
        pages, _ = parse_sitemap_xml(xml)
        assert pages == ["https://x.com/a"]


class TestParseRobotsSitemaps:
    def test_extracts_sitemap_directives(self):
        robots = (
            "User-agent: *\nDisallow: /private\n"
            "Sitemap: https://x.com/sitemap.xml\n"
            "sitemap: https://x.com/news.xml\n"
        )
        out = parse_robots_sitemaps(robots, base_url="https://x.com")
        assert out == ["https://x.com/sitemap.xml", "https://x.com/news.xml"]

    def test_relative_sitemap_absolutized(self):
        out = parse_robots_sitemaps("Sitemap: /sm.xml", base_url="https://x.com")
        assert out == ["https://x.com/sm.xml"]

    def test_no_directives_returns_empty(self):
        assert parse_robots_sitemaps("User-agent: *\nDisallow:", base_url="https://x.com") == []


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
    """Maps URL -> _Resp; missing URLs return 404."""
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, follow_redirects=False):
        self.calls.append(url)
        resp = self.routes.get(url, _Resp(404))
        if not resp.url:
            resp.url = url
        return resp


_URLSET = (
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://x.com/a</loc></url><url><loc>https://x.com/b</loc></url></urlset>"
)
_INDEX = (
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<sitemap><loc>https://x.com/child.xml</loc></sitemap></sitemapindex>"
)


class TestDiscoverSitemapUrls:
    @pytest.mark.asyncio
    async def test_uses_robots_directives(self):
        client = _StubClient({
            "https://x.com/robots.txt": _Resp(200, "Sitemap: https://x.com/custom.xml"),
        })
        out = await discover_sitemap_urls(client, "https://x.com/")
        assert out == ["https://x.com/custom.xml"]

    @pytest.mark.asyncio
    async def test_falls_back_to_common_paths(self):
        client = _StubClient({})  # robots.txt 404
        out = await discover_sitemap_urls(client, "https://x.com/")
        assert out == ["https://x.com/sitemap.xml", "https://x.com/sitemap_index.xml"]


class TestCollectSitemapUrls:
    @pytest.mark.asyncio
    async def test_follows_sitemapindex(self):
        client = _StubClient({
            "https://x.com/sitemap.xml": _Resp(200, _INDEX),
            "https://x.com/child.xml": _Resp(200, _URLSET),
        })
        out = await collect_sitemap_urls(
            client, ["https://x.com/sitemap.xml"], max_urls=100, max_sitemaps=10
        )
        assert out == ["https://x.com/a", "https://x.com/b"]

    @pytest.mark.asyncio
    async def test_respects_max_urls(self):
        client = _StubClient({"https://x.com/sitemap.xml": _Resp(200, _URLSET)})
        out = await collect_sitemap_urls(
            client, ["https://x.com/sitemap.xml"], max_urls=1, max_sitemaps=10
        )
        assert out == ["https://x.com/a"]

    @pytest.mark.asyncio
    async def test_missing_sitemap_yields_nothing(self):
        client = _StubClient({})
        out = await collect_sitemap_urls(
            client, ["https://x.com/sitemap.xml"], max_urls=100, max_sitemaps=10
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_sitemapindex_cycle_terminates(self):
        # A -> B -> A must not loop forever (seen-set guard).
        a = (
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://x.com/b.xml</loc></sitemap></sitemapindex>"
        )
        b = (
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://x.com/a.xml</loc></sitemap></sitemapindex>"
        )
        client = _StubClient({
            "https://x.com/a.xml": _Resp(200, a),
            "https://x.com/b.xml": _Resp(200, b),
        })
        out = await collect_sitemap_urls(
            client, ["https://x.com/a.xml"], max_urls=100, max_sitemaps=10
        )
        assert out == []
        assert client.calls == ["https://x.com/a.xml", "https://x.com/b.xml"]

    @pytest.mark.asyncio
    async def test_respects_max_sitemaps(self):
        index = (
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://x.com/s1.xml</loc></sitemap>"
            "<sitemap><loc>https://x.com/s2.xml</loc></sitemap></sitemapindex>"
        )
        client = _StubClient({
            "https://x.com/index.xml": _Resp(200, index),
            "https://x.com/s1.xml": _Resp(200, _URLSET),
            "https://x.com/s2.xml": _Resp(200, _URLSET),
        })
        # max_sitemaps=1 -> only the index doc is fetched, no children.
        out = await collect_sitemap_urls(
            client, ["https://x.com/index.xml"], max_urls=100, max_sitemaps=1
        )
        assert out == []
        assert client.calls == ["https://x.com/index.xml"]
