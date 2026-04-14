from __future__ import annotations
from typing import Any, Literal, Dict
from pydantic import BaseModel, Field, HttpUrl

ProxyType = Literal["none", "mobile_shared", "mobile", "res_static", "res_rotating", "dc_static"]
WaitUntil = Literal["domcontentloaded", "networkidle"]
Device = Literal["desktop", "mobile"]
ExtractType = Literal["css", "xpath"]
JobStatus = Literal["queued", "running", "done", "failed"]


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
    selector: str
    attr: str = "text"   # text | html | attribute name
    all: bool = False
    required: bool = False


class ExtractRule(BaseModel):
    type: ExtractType
    fields: Dict[str, FieldRule]


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

    proxy_type: ProxyType = "none"
    proxy_pool_id: str | None = None
    proxy_geo: ProxyGeo | None = None

    block_assets: bool | None = None
    raw_html: bool = False
    extract: ExtractRule | None = None
    screenshot: bool = False


class ScrapeMeta(BaseModel):
    url: str
    final_url: str | None = None
    status_code: int | None = None
    device: Device
    proxy_type: ProxyType
    proxy_pool_id: str | None = None
    retries: int = 0


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
