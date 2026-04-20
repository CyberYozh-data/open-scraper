from __future__ import annotations
from typing import Any, Literal, Dict
from pydantic import BaseModel, Field, HttpUrl

ProxyType = Literal["mobile_shared", "mobile", "res_static", "res_rotating", "dc_static"]
ScrapeProxyType = Literal["none", "mobile_shared", "mobile", "res_static", "res_rotating", "dc_static"]
WaitUntil = Literal["domcontentloaded", "networkidle"]
Device = Literal["desktop", "mobile"]
ExtractType = Literal["css", "xpath"]
JobStatus = Literal["queued", "running", "done", "failed"]


class ProxyItem(BaseModel):
    id: str
    url: str
    status: str
    expired: bool
    host: str | None = None
    port: int | None = None
    access_type: str | None = None


class ProxyListResponse(BaseModel):
    proxy_type: ProxyType
    category: str
    configured: bool
    items: list[ProxyItem]


class CountryItem(BaseModel):
    code: str
    name: str


class CountriesResponse(BaseModel):
    countries: list[CountryItem]


class Cookie(BaseModel):
    name: str
    value: str
    domain: str | None = None
    path: str | None = "/"
    expires: int | None = None
    httpOnly: bool | None = None
    secure: bool | None = None
    sameSite: Literal["Strict", "Lax", "None"] | None = None


class FieldRule(BaseModel):
    selector: str = Field(
        ...,
        description=(
            "CSS or XPath expression (must match the parent ExtractRule.type). "
            "Examples: 'h1', '.price_color', '#cart a' for CSS; '//h1', "
            "'//div[@class=\"item\"]/a/@href' for XPath."
        ),
    )
    attr: str = Field(
        default="text",
        description=(
            "What to pull from the matched element. One of: "
            "'text' (default, text content), 'html' (outer HTML of the node), "
            "or any HTML attribute name like 'href', 'src', 'data-id'."
        ),
    )
    all: bool = Field(
        default=False,
        description=(
            "If false (default), returns the FIRST match as a string. If true, "
            "returns a LIST of every match. Use true for repeating elements "
            "like product cards, links, table rows."
        ),
    )
    required: bool = Field(
        default=False,
        description=(
            "If true and the selector matches nothing, a warning is added to "
            "the response. The request itself still succeeds."
        ),
    )


class ExtractRule(BaseModel):
    type: ExtractType = Field(
        ...,
        description=(
            "Which selector language to use for every field: 'css' "
            "(lxml.cssselect) or 'xpath' (lxml XPath). Must match the syntax "
            "used in field selectors."
        ),
    )
    fields: Dict[str, FieldRule] = Field(
        ...,
        description=(
            "Map of {output_key: FieldRule}. The output_key is the name the "
            "extracted value will appear under in the response's data object. "
            "Example: {'title': {selector:'h1'}, 'price': {selector:'.price_color'}} "
            "-> data = {'title': '...', 'price': '...'}."
        ),
    )


class ProxyGeo(BaseModel):
    country_code: str | None = None
    region: str | None = None
    city: str | None = None


class ScrapeRequest(BaseModel):
    url: HttpUrl
    render: bool = True
    wait_until: WaitUntil = "domcontentloaded"
    wait_for_selector: str | None = None
    timeout_ms: int | None = None

    device: Device = "desktop"
    headers: Dict[str, str] | None = None
    cookies: list[Cookie] | None = None

    proxy_type: ScrapeProxyType = "none"
    proxy_pool_id: str | None = None
    proxy_geo: ProxyGeo | None = None

    block_assets: bool | None = Field(
        default=None,
        description=(
            "Block images / fonts / media during page load for speed. "
            "If unset, falls back to the BLOCK_ASSETS env var. Turn off when "
            "capturing screenshots if images are needed."
        ),
    )
    raw_html: bool = Field(
        default=False,
        description="Include the full post-render HTML in the response.",
    )
    extract: ExtractRule | None = Field(
        default=None,
        description=(
            "Optional structured extraction. When set, the response will "
            "include a 'data' object keyed by the names you chose, with the "
            "extracted values. Much cheaper than downloading raw_html and "
            "parsing yourself."
        ),
    )
    screenshot: bool = Field(
        default=False,
        description=(
            "Capture a full-page PNG screenshot (base64-encoded in the "
            "response). Triggers a scroll pass to load lazy images unless "
            "block_assets is on."
        ),
    )
    stealth: bool = Field(
        default=True,
        description=(
            "Apply playwright-stealth patches (navigator.webdriver, WebGL / "
            "Canvas fingerprint, chrome runtime) to reduce bot detection."
        ),
    )


class ScrapeMeta(BaseModel):
    url: str
    final_url: str | None = None
    status_code: int | None = None
    device: Device
    proxy_type: ScrapeProxyType
    proxy_pool_id: str | None = None
    retries: int = 0
    applied_user_agent: str | None = None
    applied_locale: str | None = None
    applied_timezone: str | None = None
    applied_accept_language: str | None = None


class ScrapeResponse(BaseModel):
    request_id: str
    took_ms: int
    meta: ScrapeMeta
    data: Dict[str, Any] | None = None
    raw_html: str | None = None
    screenshot_base64: str | None = None
    warnings: list[str] = Field(default_factory=list)


class BatchScrapeRequest(BaseModel):
    pages: list[ScrapeRequest]


class JobCreateResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    done: int
    total: int
    error: str | None = None


class JobResultsResponse(BaseModel):
    job_id: str
    status: JobStatus
    pages: list[ScrapeRequest]
    total: int
    done: int = 0
    error: str | None = None
    results: list[ScrapeResponse] | None = None
