from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas import (
    Cookie,
    FieldRule,
    ExtractRule,
    ProxyGeo,
    ScrapeRequest,
    ScrapeMeta,
    ScrapeResponse,
    BatchScrapeRequest,
    JobCreateResponse,
    JobStatusResponse,
    JobResultsResponse,
)


class TestCookie:
    def test_cookie_minimal(self):
        """Minimal Cookie model"""
        cookie = Cookie(name="session", value="abc123", domain="example.com")
        assert cookie.name == "session"
        assert cookie.value == "abc123"
        assert cookie.domain == "example.com"

    def test_cookie_defaults(self):
        """Defaults for Cookie"""
        cookie = Cookie(name="test", value="value", domain="example.com")
        assert cookie.path == "/"
        assert cookie.expires is None
        assert cookie.httpOnly is None
        assert cookie.secure is None
        assert cookie.sameSite is None

    def test_cookie_full(self):
        """Full Cookie model"""
        cookie = Cookie(
            name="auth",
            value="token123",
            domain=".example.com",
            path="/api",
            expires=1234567890,
            httpOnly=True,
            secure=True,
            sameSite="Strict",
        )
        assert cookie.name == "auth"
        assert cookie.httpOnly is True
        assert cookie.sameSite == "Strict"


class TestFieldRule:
    def test_field_rule_defaults(self):
        """Defaults for FieldRule"""
        rule = FieldRule(selector="h1")
        assert rule.selector == "h1"
        assert rule.attr == "text"
        assert rule.all is False
        assert rule.required is False

    def test_field_rule_custom(self):
        """Custom values FieldRule"""
        rule = FieldRule(
            selector=".items",
            attr="html",
            all=True,
            required=True,
        )
        assert rule.selector == ".items"
        assert rule.attr == "html"
        assert rule.all is True
        assert rule.required is True


class TestExtractRule:
    def test_extract_rule_css(self):
        """ExtractRule with CSS selector"""
        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1"),
            },
        )
        assert rule.type == "css"
        assert "title" in rule.fields
        assert rule.fields["title"].selector == "h1"

    def test_extract_rule_xpath(self):
        """ExtractRule with XPath selector"""
        rule = ExtractRule(
            type="xpath",
            fields={
                "title": FieldRule(selector="//h1/text()"),
            },
        )
        assert rule.type == "xpath"

    def test_extract_rule_multiple_fields(self):
        """ExtractRule with a few fields"""
        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1"),
                "links": FieldRule(selector="a", attr="href", all=True),
            },
        )
        assert len(rule.fields) == 2
        assert "title" in rule.fields
        assert "links" in rule.fields


class TestProxyGeo:
    def test_proxy_geo_all_none(self):
        """ProxyGeo with all fields None"""
        geo = ProxyGeo()
        assert geo.country_code is None
        assert geo.region is None
        assert geo.city is None

    def test_proxy_geo_country_only(self):
        """ProxyGeo with country_code only"""
        geo = ProxyGeo(country_code="US")
        assert geo.country_code == "US"
        assert geo.region is None
        assert geo.city is None

    def test_proxy_geo_full(self):
        """ProxyGeo with all fields"""
        geo = ProxyGeo(country_code="US", region="California", city="Los Angeles")
        assert geo.country_code == "US"
        assert geo.region == "California"
        assert geo.city == "Los Angeles"


class TestScrapeRequest:
    def test_scrape_request_minimal(self):
        """Minimal ScrapeRequest"""
        request = ScrapeRequest(url="https://example.com")
        assert str(request.url) == "https://example.com/"
        assert request.render is True
        assert request.wait_until == "domcontentloaded"
        assert request.device == "desktop"
        assert request.proxy_type == "none"

    def test_scrape_request_defaults(self):
        """Defaults for ScrapeRequest"""
        request = ScrapeRequest(url="https://example.com")
        assert request.wait_for_selector is None
        assert request.timeout_ms is None
        assert request.headers is None
        assert request.cookies is None
        assert request.proxy_pool_id is None
        assert request.proxy_geo is None
        assert request.block_assets is None
        assert request.raw_html is False
        assert request.extract is None
        assert request.screenshot is False

    def test_scrape_request_full(self):
        """Full ScrapeRequest"""
        request = ScrapeRequest(
            url="https://example.com",
            render=True,
            wait_until="networkidle",
            wait_for_selector=".content",
            timeout_ms=60000,
            device="mobile",
            headers={"User-Agent": "test"},
            cookies=[Cookie(name="test", value="123", domain="example.com")],
            proxy_type="res_rotating",
            proxy_pool_id="pool_1",
            proxy_geo=ProxyGeo(country_code="GB", city="London"),
            block_assets=True,
            raw_html=True,
            extract=ExtractRule(type="css", fields={"title": FieldRule(selector="h1")}),
            screenshot=True,
        )
        assert request.wait_until == "networkidle"
        assert request.device == "mobile"
        assert request.proxy_type == "res_rotating"
        assert request.proxy_geo.country_code == "GB"
        assert request.proxy_geo.city == "London"
        assert request.raw_html is True
        assert request.screenshot is True

    def test_scrape_request_url_validation(self):
        """Validation URL"""
        with pytest.raises(ValidationError):
            ScrapeRequest(url="not-a-url")


class TestScrapeMeta:
    def test_scrape_meta(self):
        """ScrapeMeta model"""
        meta = ScrapeMeta(
            url="https://example.com",
            final_url="https://example.com/page",
            status_code=200,
            device="desktop",
            proxy_type="none",
            retries=0,
        )
        assert meta.url == "https://example.com"
        assert meta.final_url == "https://example.com/page"
        assert meta.status_code == 200
        assert meta.retries == 0


class TestScrapeResponse:
    def test_scrape_response(self):
        """ScrapeResponse model"""
        response = ScrapeResponse(
            request_id="req_123",
            took_ms=1500,
            meta=ScrapeMeta(
                url="https://example.com",
                device="desktop",
                proxy_type="none",
                retries=0,
            ),
        )
        assert response.request_id == "req_123"
        assert response.took_ms == 1500
        assert response.warnings == []

    def test_scrape_response_with_data(self):
        """ScrapeResponse with datas"""
        response = ScrapeResponse(
            request_id="req_123",
            took_ms=1000,
            meta=ScrapeMeta(
                url="https://example.com",
                device="desktop",
                proxy_type="none",
                retries=0,
            ),
            data={"title": "Test"},
            warnings=["test warning"],
        )
        assert response.data == {"title": "Test"}
        assert len(response.warnings) == 1


class TestBatchScrapeRequest:
    def test_batch_scrape_request(self):
        """BatchScrapeRequest model"""
        request = BatchScrapeRequest(
            pages=[
                ScrapeRequest(url="https://example.com"),
                ScrapeRequest(url="https://example.org"),
            ]
        )
        assert len(request.pages) == 2


class TestJobResponses:
    def test_job_create_response(self):
        """JobCreateResponse model"""
        response = JobCreateResponse(job_id="job_123")
        assert response.job_id == "job_123"

    def test_job_status_response(self):
        """JobStatusResponse model"""
        response = JobStatusResponse(
            job_id="job_123",
            status="running",
            done=5,
            total=10,
        )
        assert response.job_id == "job_123"
        assert response.status == "running"
        assert response.done == 5
        assert response.total == 10
        assert response.error is None

    def test_job_results_response_done(self):
        """JobResultsResponse for done job"""
        response = JobResultsResponse(
            job_id="job_123",
            status="done",
            pages=[],
            total=1,
            done=1,
            results=[],
        )
        assert response.job_id == "job_123"
        assert response.status == "done"
        assert response.results == []
        assert response.error is None

    def test_job_results_response_failed(self):
        """JobResultsResponse for failed job"""
        response = JobResultsResponse(
            job_id="job_123",
            status="failed",
            pages=[],
            total=1,
            done=0,
            error="Worker crashed",
            results=None,
        )
        assert response.status == "failed"
        assert response.error == "Worker crashed"
        assert response.results is None
