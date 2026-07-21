from __future__ import annotations

import logging
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # =====================
    # Service
    # =====================
    host: str = Field(
        default="0.0.0.0",
        alias="HOST",
        description="Host address to bind the HTTP server to.",
    )

    port: int = Field(
        default=8000,
        alias="PORT",
        description="Port number for the HTTP server.",
    )

    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="Logging level (DEBUG, INFO, WARNING, ERROR).",
    )

    # =====================
    # Playwright
    # =====================
    headless: bool = Field(
        default=True,
        alias="HEADLESS",
        description=(
            "Default browser launch mode. A request's `headless` field "
            "overrides it per scrape; a request asking for the non-default "
            "mode gets a throwaway browser, so the warm pool never holds both "
            "modes. Xvfb runs regardless, so headful requests work even when "
            "this is true."
        ),
    )
    chrome_channel: str | None = Field(
        default=None,
        alias="CHROME_CHANNEL",
        description=(
            "Chromium engine channel. None (default) uses Playwright's bundled "
            "Chromium. Set to 'chrome' to drive a real Google Chrome installed in "
            "the image (better anti-bot: real branding/codecs + a populated "
            "navigator.plugins, and a newer engine than the bundled build) — "
            "requires `playwright install chrome` in the Dockerfile. The UA major "
            "auto-tracks whatever engine actually launches."
        ),
    )
    block_assets: bool = Field(
        default=True,
        alias="BLOCK_ASSETS",
        description="Block images, fonts and media to speed up scraping.",
    )
    request_timeout_ms: int = Field(
        default=30_000,
        alias="REQUEST_TIMEOUT_MS",
        description="Default page request timeout in milliseconds.",
    )
    warmup_dwell_ms: int = Field(
        default=2500,
        alias="WARMUP_DWELL_MS",
        description="Default dwell (ms) on the origin during a 'homepage' warmup before the real navigation.",
    )
    max_retries: int = Field(
        default=3,
        alias="MAX_RETRIES",
        description=(
            "Maximum number of fetch attempts per request when a failure looks "
            "like a proxy issue. Only applies when a proxy is used; direct "
            "connections never retry."
        ),
    )
    webrtc_block: bool = Field(
        default=True,
        alias="WEBRTC_BLOCK",
        description=(
            "Prevent WebRTC from leaking the real IP around the proxy by "
            "launching Chromium with --webrtc-ip-handling-policy=disable_"
            "non_proxied_udp. Set to false only if you need WebRTC features "
            "(video chat, RTCPeerConnection, etc.) inside scraped pages."
        ),
    )

    # =====================
    # Workers
    # =====================
    workers: int = Field(
        default=2,
        alias="WORKERS",
        description=(
            "taskiq worker processes per scraper-worker container (the "
            "`--workers` flag); also reported by /health for visibility."
        ),
    )
    queue_maxsize: int = Field(
        default=200,
        alias="QUEUE_MAXSIZE",
        description="Submit backpressure: reject new jobs once the Redis stream depth exceeds this.",
    )
    job_result_ttl_s: float = Field(
        default=600.0,
        ge=0,
        alias="JOB_RESULT_TTL_S",
        description=(
            "Seconds a finished job's keys (status + results) live in Redis "
            "after finalization, via a native key TTL. 0 disables the TTL. "
            "Keep comfortably above how long a consumer takes to fetch /results."
        ),
    )
    browser_idle_shutdown_s: float = Field(
        default=600.0,
        ge=0,
        alias="BROWSER_IDLE_SHUTDOWN_S",
        description=(
            "Seconds of inactivity after which a worker closes its Chromium "
            "instance to free memory. The browser is re-launched lazily on "
            "the next job (cold start adds ~1-2s of latency to that one "
            "job). Set to 0 to keep browsers warm forever — lower first-job "
            "latency at the cost of ~400-500 MB RSS per worker held "
            "permanently."
        ),
    )
    browser_idle_shutdown_s_secondary: int = Field(
        default=120,
        alias="BROWSER_IDLE_SHUTDOWN_S_SECONDARY",
        description=(
            "Idle window (seconds) for non-default native engines (e.g. a warm "
            "Firefox). Shorter than the primary so secondary engines reclaim RAM "
            "quickly. 0 disables."
        ),
    )

    # =====================
    # Queue (taskiq + Redis)
    # =====================
    redis_url: str = Field(
        default="redis://redis:6379/0",
        alias="REDIS_URL",
        description="Redis connection URL (broker, job store, session store).",
    )
    page_task_timeout_s: float = Field(
        default=120.0,
        alias="PAGE_TASK_TIMEOUT_S",
        description=(
            "Hard ceiling for one scrape_page task (all retry attempts "
            "included). Even a hung Playwright produces an error slot within "
            "this bound. Replaces the old JOB_TIMEOUT_MS role."
        ),
    )
    login_task_timeout_s: float = Field(
        default=45.0,
        alias="LOGIN_TASK_TIMEOUT_S",
        description="Hard ceiling for the worker's login task (the actual login work).",
    )
    login_result_grace_s: float = Field(
        default=15.0,
        ge=0,
        alias="LOGIN_RESULT_GRACE_S",
        description=(
            "Extra time the login endpoint waits for the result beyond "
            "LOGIN_TASK_TIMEOUT_S. The worker's timeout is measured from when it "
            "picks the task up, the API's from enqueue; this grace absorbs the "
            "queue-pickup skew so a slow-but-successful login isn't 504'd (which "
            "would mark the session failed and drop its storage_state)."
        ),
    )
    reclaim_idle_s: float = Field(
        default=240.0,
        alias="RECLAIM_IDLE_S",
        description=(
            "Pending stream entries idle longer than this are reclaimed and "
            "re-enqueued (worker died mid-task). Keep ~2x PAGE_TASK_TIMEOUT_S."
        ),
    )
    browser_max_pages: int = Field(
        default=100,
        alias="BROWSER_MAX_PAGES",
        description=(
            "Browser retirement: a worker recycles its browser after this many "
            "pages to bound Chromium memory bloat. 0 disables."
        ),
    )

    # =====================
    # Sessions (Phase 1)
    # =====================
    sessions_max: int = Field(
        default=256,
        alias="SESSIONS_MAX",
        description="Soft global cap on simultaneous sessions (Redis ZSET-backed). LRU-evicted on overflow.",
    )
    session_storage_state_max_bytes: int = Field(
        default=2 * 1024 * 1024,
        alias="SESSION_STORAGE_STATE_MAX_BYTES",
        description="Per-session storage_state byte cap. Rejected on overflow.",
    )

    # =====================
    # Service-to-service auth
    # =====================
    service_token: SecretStr | None = Field(
        default=None,
        alias="SERVICE_TOKEN",
        description=(
            "Shared secret guarding credential-bearing internal endpoints "
            "(GET /api/v1/proxies/resolve). Callers on the internal network "
            "(the crawler's /map) present it as the X-Service-Token header. "
            "When unset, those endpoints refuse to serve rather than leak "
            "proxy credentials (fail-closed)."
        ),
    )

    # =====================
    # Proxy (CyberYozh)
    # =====================
    cyberyozh_api_key: SecretStr | None = Field(
        default=None,
        alias="CYBERYOZH_API_KEY",
        description="API key for CyberYozh proxy service.",
    )
    cyberyozh_base_url: str = Field(
        default="https://app.cyberyozh.com",
        alias="CYBERYOZH_BASE_URL",
        description="Base URL for CyberYozh API.",
    )

    # =====================
    # LLM (presets)
    # =====================
    openai_api_key: SecretStr | None = Field(
        default=None,
        alias="OPENAI_API_KEY",
        description="Server-side OpenAI key for openai/* models.",
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        alias="ANTHROPIC_API_KEY",
        description="Server-side Anthropic key for anthropic/* models.",
    )
    gemini_api_key: SecretStr | None = Field(
        default=None,
        alias="GEMINI_API_KEY",
        description="Server-side Gemini key for gemini/* models.",
    )
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        alias="OPENROUTER_API_KEY",
        description="Server-side OpenRouter key for openrouter/* models.",
    )
    custom_llm_base_url: str | None = Field(
        default=None,
        alias="CUSTOM_LLM_BASE_URL",
        description="OpenAI-compatible base URL for custom/* models.",
    )
    custom_llm_api_key: SecretStr | None = Field(
        default=None,
        alias="CUSTOM_LLM_API_KEY",
        description="API key for the custom OpenAI-compatible endpoint.",
    )
    default_llm_model: str = Field(
        default="openai/gpt-5.4-mini",
        alias="DEFAULT_LLM_MODEL",
        description="Model used when a preset/request doesn't specify one.",
    )
    default_proxy_country: str = Field(
        default="GB",
        alias="DEFAULT_PROXY_COUNTRY",
        description=(
            "ISO-3166 alpha-2 country pinned for residential rotating proxies "
            "(res_rotating / prem_res_rotating) when a request supplies no "
            "proxy_geo.country_code. Without it the exit lands in a random "
            "country while the browser timezone/locale stay this country's "
            "defaults — a fingerprint mismatch. GB maps to Europe/London + "
            "en-GB, so the exit and browser timezone/locale stay aligned. GB "
            "is the default rather than US because several targets throttle "
            "or block the US residential exit range."
        ),
    )
    preset_llm_max_tokens: int = Field(
        default=4000,
        alias="PRESET_LLM_MAX_TOKENS",
        description="Token ceiling for a single self-heal / extraction call.",
    )
    preset_llm_timeout_s: float = Field(
        default=30.0,
        alias="PRESET_LLM_TIMEOUT_S",
        description=(
            "Hard timeout for a single LLM call. Bounds the in-worker "
            "self-heal/extract round-trip so a hung provider socket can't "
            "wedge a worker process past the job timeout."
        ),
    )



settings = Settings()


class LogTagFilter(logging.Filter):
    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag

    def filter(self, record: logging.LogRecord) -> bool:
        record.tag = self.tag
        return True


def setup_logging(level: str = "INFO", tag: Optional[str] = None) -> None:
    tag = tag or "M"
    level_value = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level_value)

    for _handler in list(root.handlers):
        root.removeHandler(_handler)

    handler = logging.StreamHandler()
    handler.setLevel(level_value)

    _format = "%(asctime)s | %(levelname)s | [%(tag)s] | %(name)s | %(message)s"
    handler.setFormatter(logging.Formatter(_format))
    handler.addFilter(LogTagFilter(tag))
    root.addHandler(handler)

    for loger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _logger = logging.getLogger(loger_name)
        _logger.handlers = []
        _logger.propagate = True
        _logger.setLevel(level_value)

    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)
