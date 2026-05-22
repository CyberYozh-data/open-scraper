from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.schemas import Device, ProxyGeo, ScrapeProxyType

SessionStatus = Literal["created", "logging_in", "ready", "expired", "failed"]

LoginOp = Literal[
    "goto",
    "fill",
    "click",
    "wait_for_selector",
    "wait_for_timeout",
    "press_key",
    "type_text",
    "hover",
]


class LoginStep(BaseModel):
    op: LoginOp
    selector: str | None = None
    url: str | None = None
    value: str | None = Field(
        default=None,
        description="Supports $creds_<key> substitution from the login request's `creds` dict.",
    )
    key: str | None = None
    timeout_ms: int | None = Field(default=None, ge=0, le=120_000)
    ms: int | None = Field(default=None, ge=0, le=120_000)


class LoginScript(BaseModel):
    steps: list[LoginStep]
    success_selector: str | None = None
    success_url_regex: str | None = None


class SessionCreateRequest(BaseModel):
    device: Device = "desktop"
    proxy_type: ScrapeProxyType = "none"
    proxy_pool_id: str | None = None
    proxy_geo: ProxyGeo | None = None
    ttl_seconds: int = Field(default=86400, ge=300, le=7 * 86400)


class SessionLoginRequest(BaseModel):
    script: LoginScript
    creds: dict[str, str] = Field(
        ...,
        description=(
            "Free-form credential bag. Keys become $creds_<key> placeholders in "
            "LoginStep.value/url. Lives in RAM for the duration of this request "
            "only; never persisted."
        ),
    )


class SessionLoginResult(BaseModel):
    ok: bool
    failed_step_index: int | None = None
    failed_step: LoginStep | None = None
    error: str | None = None
    screenshot_b64: str | None = None
    took_ms: int = 0


class SessionRecord(BaseModel):
    session_id: str
    status: SessionStatus
    created_at: float
    expires_at: float
    last_used_at: float

    device: Device
    proxy_type: ScrapeProxyType
    proxy_pool_id: str | None = None
    proxy_geo: ProxyGeo | None = None

    storage_state: dict[str, Any] | None = None
    storage_state_bytes: int = 0
    login_script: LoginScript | None = None
    last_error: str | None = None


class SessionPublic(BaseModel):
    """`SessionRecord` minus `storage_state` — what `GET /sessions/{id}` returns."""

    session_id: str
    status: SessionStatus
    created_at: float
    expires_at: float
    last_used_at: float
    device: Device
    proxy_type: ScrapeProxyType
    proxy_pool_id: str | None = None
    proxy_geo: ProxyGeo | None = None
    storage_state_bytes: int = 0
    last_error: str | None = None


class SessionList(BaseModel):
    items: list[SessionPublic]


class SessionCookieInjection(BaseModel):
    cookies: list[dict[str, Any]]


class SessionCreated(BaseModel):
    session_id: str
    expires_at: float
