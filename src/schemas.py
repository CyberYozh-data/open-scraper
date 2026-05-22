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
WaitUntil = Literal["domcontentloaded", "networkidle"]
Device = Literal["desktop", "mobile"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]


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
    screenshot_base64: str | None = None
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
