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
    WarmupOptions,
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

    def test_proxy_geo_rejects_country_code_injection(self):
        # A hyphenated/over-long country_code would inject extra '-'-delimited
        # targeting tokens into the CyberYozh proxy username — reject at the
        # boundary (must be an ISO-3166 alpha-2 code).
        with pytest.raises(ValidationError):
            ProxyGeo(country_code="ru-r-77-ct-moscow")
        with pytest.raises(ValidationError):
            ProxyGeo(country_code="USA")

    def test_proxy_geo_country_code_normalizes_blank(self):
        # Blank means "no country" (preserves the legacy `or ""` behaviour);
        # a valid code is accepted in either case and trimmed.
        assert ProxyGeo(country_code="").country_code is None
        assert ProxyGeo(country_code="  ").country_code is None
        assert ProxyGeo(country_code=" us ").country_code == "us"


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

    def test_applied_warmup_accepts_server_dwell_over_request_bound(self):
        # applied_warmup records what the warmup ACTUALLY did. The server's
        # WARMUP_DWELL_MS has no upper bound, so the read-back model must not
        # impose the request-side le=60000 on dwell_ms — otherwise a successful
        # scrape would 500 on /results (store.get_full -> ScrapeResponse.model_validate).
        resp = ScrapeResponse.model_validate({
            "request_id": "r1",
            "took_ms": 1,
            "meta": {
                "url": "https://e.com",
                "device": "desktop",
                "proxy_type": "none",
                "applied_warmup": {
                    "type": "homepage", "url": "https://e.com/", "dwell_ms": 70000,
                },
            },
        })
        assert resp.meta.applied_warmup.dwell_ms == 70000
        assert resp.meta.applied_warmup.url == "https://e.com/"

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

    def test_job_results_response_allows_unfinished_slots(self):
        # While a batch runs, results is seeded [None] * n and filled per page
        # (jobs.py); the response model must serialize that snapshot instead of
        # 500ing on anyone who polls /results before terminal status.
        resp = JobResultsResponse(
            job_id="req_test",
            status="running",
            pages=[ScrapeRequest(url="https://example.com")],
            total=1,
            results=[None],
        )
        assert resp.results == [None]


class TestScrapeRequestElementSelector:
    def test_element_selector_defaults_to_none(self):
        scrape_request = ScrapeRequest(url="https://example.com")
        assert scrape_request.element_selector is None

    def test_element_selector_accepts_empty_string(self):
        scrape_request = ScrapeRequest(url="https://example.com", element_selector="")
        assert scrape_request.element_selector == ""

    def test_element_selector_accepts_valid_css(self):
        scrape_request = ScrapeRequest(url="https://example.com", element_selector="#main .alert")
        assert scrape_request.element_selector == "#main .alert"

    def test_element_selector_rejects_too_long(self):
        with pytest.raises(ValidationError):
            ScrapeRequest(url="https://example.com", element_selector="x" * 2049)

    def test_element_selector_max_length_boundary(self):
        scrape_request = ScrapeRequest(url="https://example.com", element_selector="x" * 2048)
        assert len(scrape_request.element_selector) == 2048


class TestScrapeResponseElementStatus:
    def _meta(self):
        return ScrapeMeta(
            url="https://example.com",
            device="desktop",
            proxy_type="none",
        )

    def test_status_defaults_to_none(self):
        resp = ScrapeResponse(request_id="r1", took_ms=10, meta=self._meta())
        assert resp.element_screenshot_status is None

    def test_status_accepts_element_value(self):
        resp = ScrapeResponse(
            request_id="r1", took_ms=10, meta=self._meta(),
            element_screenshot_status="element",
        )
        assert resp.element_screenshot_status == "element"

    @pytest.mark.parametrize(
        "value",
        [
            "fallback_not_found",
            "fallback_invalid",
            "fallback_zero_size",
            "fallback_timeout",
            "not_requested",
            "no_screenshot",
        ],
    )
    def test_status_accepts_all_literal_values(self, value):
        resp = ScrapeResponse(
            request_id="r1", took_ms=10, meta=self._meta(),
            element_screenshot_status=value,
        )
        assert resp.element_screenshot_status == value

    def test_status_rejects_bogus_value(self):
        with pytest.raises(ValidationError):
            ScrapeResponse(
                request_id="r1", took_ms=10, meta=self._meta(),
                element_screenshot_status="bogus",
            )


class TestMarkdownContract:
    def _meta(self):
        return ScrapeMeta(url="https://x.com", device="desktop", proxy_type="none")

    def test_request_accepts_formats_and_options(self):
        req = ScrapeRequest(
            url="https://x.com",
            formats=["markdown", "fit_markdown"],
            markdown_options={"content_filter": "pruning", "citations": True},
        )
        assert req.formats == ["markdown", "fit_markdown"]
        assert req.markdown_options.content_filter == "pruning"

    def test_request_rejects_bogus_format(self):
        with pytest.raises(ValidationError):
            ScrapeRequest(url="https://x.com", formats=["nope"])

    def test_response_markdown_fields_default_none(self):
        resp = ScrapeResponse(request_id="r1", took_ms=10, meta=self._meta())
        assert resp.markdown is None
        assert resp.fit_markdown is None
        assert resp.markdown_references is None
        assert resp.links is None
        assert resp.html is None


def test_scrape_request_accepts_wait_until_load():
    # yozh-law-checker retries timed-out seed renders with wait_until="load";
    # rejecting it turns the rescue path into a 422 (audit H1).
    req = ScrapeRequest(url="https://example.com", wait_until="load")
    assert req.wait_until == "load"


# ---------------------------------------------------------------------------
# Task A1: prem_res_rotating proxy type + PremProxyOptions schema
# ---------------------------------------------------------------------------

from src.schemas import PremProxyOptions  # noqa: E402


def test_prem_proxy_options_defaults():
    o = PremProxyOptions()
    assert o.ip_filter == "max-size-security"
    assert o.session_type == "rotating"
    assert o.protocol == "http"
    assert o.zip is None and o.sticky_id is None


def test_prem_rotation_minutes_requires_sticky():
    with pytest.raises(ValidationError):
        PremProxyOptions(session_type="rotating", rotation_minutes=5)
    # sticky is fine
    PremProxyOptions(session_type="sticky", rotation_minutes=5)


def test_prem_sticky_id_requires_sticky_session():
    # A sticky_id with the default rotating session was silently dropped before;
    # surface it as an error (mirrors the rotation_minutes guard).
    with pytest.raises(ValidationError):
        PremProxyOptions(session_type="rotating", sticky_id="abc123")
    assert PremProxyOptions(session_type="sticky", sticky_id="abc123").sticky_id == "abc123"
    # blank sticky_id normalizes to None, so it's fine with the default session
    assert PremProxyOptions(sticky_id="").sticky_id is None


def test_prem_sticky_id_rejects_token_injection():
    # sticky_id is templated raw into the username token "s-<id>"; a '-' would
    # open new gateway targeting tokens. Must be alphanumeric and bounded.
    with pytest.raises(ValidationError):
        PremProxyOptions(session_type="sticky", sticky_id="x-filter-iqs-ttl-1m")
    with pytest.raises(ValidationError):
        PremProxyOptions(session_type="sticky", sticky_id="a" * 65)
    # plain alphanumeric is accepted; blank normalizes to None (auto-generated)
    assert PremProxyOptions(session_type="sticky", sticky_id="Ab3xK9pQ").sticky_id == "Ab3xK9pQ"
    assert PremProxyOptions(session_type="sticky", sticky_id="").sticky_id is None


def test_scrape_request_accepts_prem_type_and_options():
    req = ScrapeRequest(
        url="https://yandex.ru/search/?text=x",
        proxy_type="prem_res_rotating",
        prem_proxy_options=PremProxyOptions(ip_filter="quality-security"),
    )
    assert req.proxy_type == "prem_res_rotating"
    assert req.prem_proxy_options.ip_filter == "quality-security"


def test_zip_excludes_region_and_city():
    with pytest.raises(ValidationError):
        ScrapeRequest(
            url="https://yandex.ru/search/?text=x",
            proxy_type="prem_res_rotating",
            proxy_geo={"country_code": "RU", "city": "Moscow"},
            prem_proxy_options=PremProxyOptions(zip="101000"),
        )


def test_zip_excludes_region():
    with pytest.raises(ValidationError):
        ScrapeRequest(
            url="https://yandex.ru/search/?text=x",
            proxy_type="prem_res_rotating",
            proxy_geo={"country_code": "RU", "region": "Moscow Oblast"},
            prem_proxy_options=PremProxyOptions(zip="101000"),
        )


# ---------------------------------------------------------------------------
# Task C1: WarmupOptions schema + settings.warmup_dwell_ms
# ---------------------------------------------------------------------------

def test_warmup_options_defaults():
    w = WarmupOptions()
    assert w.type == "homepage" and w.dwell_ms is None
    req = ScrapeRequest(url="https://yandex.ru/search/?text=x", warmup={"type": "homepage"})
    assert req.warmup.type == "homepage"


def test_warmup_custom_requires_valid_url():
    # custom without a url is rejected
    with pytest.raises(ValidationError):
        WarmupOptions(type="custom")
    # custom with a non-http url is rejected
    with pytest.raises(ValidationError):
        WarmupOptions(type="custom", url="notaurl")
    # custom with a proper url is accepted
    w = WarmupOptions(type="custom", url="https://example.com/seed")
    assert w.type == "custom" and w.url == "https://example.com/seed"
    # homepage ignores url (no requirement)
    assert WarmupOptions(type="homepage").url is None
