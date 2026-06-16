from __future__ import annotations
from typing import Any, Literal, Dict
from pydantic import BaseModel, Field, HttpUrl

# Single source of truth for extraction models. `FieldRule`/`ExtractRule`
# (and `PostProcess`) live in src.extract.models — the worker validates the
# same classes, so a preset's parsing_instructions flows through
# ScrapeRequest.extract without losing post_process/type.
# pylint: disable=unused-import
from src.extract.models import (  # noqa: F401  re-exported for API consumers
    ExtractRule,
    ExtractType,
    FieldRule,
    PostProcess,
)
from src.presets.models import ParserPlan, PresetMeta

# pylint: enable=unused-import

ProxyType = Literal["mobile_shared", "mobile", "res_static", "res_rotating", "dc_static"]
ScrapeProxyType = Literal["none", "mobile_shared", "mobile", "res_static", "res_rotating", "dc_static"]
SearchEngine = Literal["google", "bing", "yandex"]
WaitUntil = Literal["domcontentloaded", "networkidle"]
Device = Literal["desktop", "mobile"]
OutputFormat = Literal["markdown", "fit_markdown", "raw_html", "html", "links", "screenshot"]
ContentFilter = Literal["none", "pruning", "llm"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
ElementScreenshotStatus = Literal[
    "element",
    "fallback_not_found",
    "fallback_invalid",
    "fallback_zero_size",
    "fallback_timeout",
    "not_requested",
    "no_screenshot",
]


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


class ProxyResolveResponse(BaseModel):
    proxy_url: str | None = Field(
        default=None,
        description="Resolved upstream proxy URL (e.g. socks5://… or http://… with embedded creds), or null for proxy_type=none / no proxy.",
    )


class Cookie(BaseModel):
    name: str
    value: str
    domain: str | None = None
    path: str | None = "/"
    expires: int | None = None
    httpOnly: bool | None = None
    secure: bool | None = None
    sameSite: Literal["Strict", "Lax", "None"] | None = None


class ProxyGeo(BaseModel):
    country_code: str | None = None
    region: str | None = None
    city: str | None = None


class MarkdownOptions(BaseModel):
    only_main_content: bool = Field(
        default=False,
        description=(
            "Strip navigation / header / footer / cookie / sidebar boilerplate "
            "before conversion. Off by default: a faithful full-page render is "
            "safer for callers that audit footer/cookie/legal elements."
        ),
    )
    content_filter: ContentFilter = Field(
        default="none",
        description=(
            "How to produce `fit_markdown` (the noise-filtered variant). "
            "'pruning' = cheap heuristic; 'llm' = model-based clean-up. "
            "'none' means no fit_markdown unless the `fit_markdown` format is "
            "requested, in which case 'pruning' is used."
        ),
    )
    filter_instruction: str | None = Field(
        default=None,
        description="Natural-language instruction for the 'llm' content filter.",
    )
    filter_model: str | None = Field(
        default=None,
        description="Model for the 'llm' content filter (defaults to the server's DEFAULT_LLM_MODEL).",
    )
    ignore_links: bool = False
    ignore_images: bool = False
    citations: bool = Field(
        default=False,
        description="Replace inline links with numbered ⟨n⟩ markers + a references section.",
    )
    body_width: int = Field(
        default=0,
        description="Hard wrap width; 0 disables wrapping (recommended for LLMs).",
    )


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

    session_id: str | None = Field(
        default=None,
        description=(
            "Use a previously authenticated server-side session. When set, "
            "the server pulls cookies + storage_state from the session and "
            "writes any new state back. Mutually exclusive with `cookies`. "
            "Must match the session's pinned device/proxy_type/proxy_pool_id/proxy_geo."
        ),
    )

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
    formats: list[OutputFormat] | None = Field(
        default=None,
        description=(
            "Requested output formats. When omitted, legacy behaviour applies "
            "(driven by the raw_html / screenshot booleans). When set, the "
            "effective outputs are the union of this list and those booleans, "
            "so existing callers are unaffected. 'markdown' / 'fit_markdown' "
            "are the new LLM-ready outputs."
        ),
    )
    markdown_options: MarkdownOptions | None = Field(
        default=None,
        description="Tuning for markdown / fit_markdown output. Ignored unless a markdown format is produced.",
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
    element_selector: str | None = Field(
        default=None,
        max_length=2048,
        description=(
            "Optional CSS selector. When set together with `screenshot=true`, "
            "the returned `screenshot_base64` captures only the matching "
            "element (plus 24px padding) instead of the full page. If the "
            "selector does not match, is invalid, or matches a zero-size "
            "element, the response falls back to the full-page screenshot "
            "and `element_screenshot_status` reports the reason. Ignored "
            "when `screenshot=false`."
        ),
    )
    stealth: bool = Field(
        default=True,
        description=(
            "Apply playwright-stealth patches (navigator.webdriver, WebGL / "
            "Canvas fingerprint, chrome runtime) to reduce bot detection."
        ),
    )
    preset_meta: PresetMeta | None = Field(
        default=None,
        description=(
            "Internal — populated by the preset materializer, not by direct "
            "callers. Echoes which preset/locale/version produced this "
            "request so the worker can surface it on the response meta."
        ),
    )
    parser_plan: ParserPlan | None = Field(
        default=None,
        description=(
            "Internal — populated by the preset materializer. Carries the "
            "self-heal / AI-extraction config the worker runs after render."
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
    applied_preset: PresetMeta | None = None


class ScrapeResponse(BaseModel):
    request_id: str
    took_ms: int
    meta: ScrapeMeta
    data: Dict[str, Any] | None = None
    raw_html: str | None = None
    markdown: str | None = Field(
        default=None,
        description="LLM-ready Markdown of the page. Populated when the 'markdown' format is requested.",
    )
    fit_markdown: str | None = Field(
        default=None,
        description="Noise-filtered Markdown (pruning or LLM). Populated when the 'fit_markdown' format is requested.",
    )
    markdown_references: str | None = Field(
        default=None,
        description="References section for ⟨n⟩ citation markers, when markdown_options.citations is on.",
    )
    links: list[str] | None = Field(
        default=None,
        description="Absolute hyperlinks found on the page. Populated when the 'links' format is requested.",
    )
    html: str | None = Field(
        default=None,
        description="Cleaned HTML (boilerplate-stripped per markdown_options). Populated when the 'html' format is requested.",
    )
    screenshot_base64: str | None = None
    element_screenshot_status: ElementScreenshotStatus | None = Field(
        default=None,
        description=(
            "Diagnostic field for the element-screenshot mode. None on "
            "responses produced before the field existed; legacy clients "
            "that never read it are unaffected. `element` means "
            "screenshot_base64 is the element crop. Any `fallback_*` value "
            "means full-page was returned because the element capture "
            "failed for the named reason. `not_requested` means no selector "
            "was passed. `no_screenshot` means screenshot=false or capture "
            "failed entirely (in that case screenshot_base64 is also None)."
        ),
    )
    warnings: list[str] = Field(default_factory=list)


class BatchScrapeRequest(BaseModel):
    pages: list[ScrapeRequest]
    session_id: str | None = Field(
        default=None,
        description=(
            "Apply this session_id to every page in `pages`. Rejected (422) if "
            "any page already has a different session_id."
        ),
    )


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


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query.")
    engine: SearchEngine = Field(
        default="google",
        description="SERP engine: google (default), bing, or yandex.",
    )
    locale: str | None = Field(
        default=None,
        description="Engine locale key (us/uk/de/fr/ru/jp); preset default if unset.",
    )
    limit: int = Field(default=10, ge=1, le=50, description="Max results to return.")
    scrape: bool = Field(
        default=False,
        description="Also scrape each result page and attach the response.",
    )
    scrape_options: Dict[str, Any] | None = Field(
        default=None,
        description=(
            "Forwarded to each result's ScrapeRequest when scrape=true — any "
            "ScrapeRequest field except url, e.g. {\"raw_html\": true} or "
            "{\"formats\": [\"markdown\"]} where supported."
        ),
    )
    # Proxy override for the SERP fetch (and per-result scrapes when scrape=true).
    # When unset, the SERP keeps the google_search preset's own proxy. Useful to
    # route the Google fetch through a residential/mobile pool that isn't blocked.
    proxy_type: ScrapeProxyType | None = Field(
        default=None,
        description="Override the SERP preset's proxy_type; preset default if unset.",
    )
    proxy_pool_id: str | None = Field(
        default=None, description="Pin a proxy pool id (with proxy_type)."
    )
    proxy_geo: ProxyGeo | None = Field(
        default=None, description="Pin proxy geo (country_code/region/city)."
    )


class SearchResult(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None
    scrape: ScrapeResponse | None = Field(
        default=None, description="Per-result scrape response when scrape=true."
    )


class SearchResponse(BaseModel):
    query: str
    count: int
    results: list[SearchResult]
    took_ms: int = 0  # wall-clock time of the search (SERP + optional scrapes)
    warnings: list[str] = Field(default_factory=list)
