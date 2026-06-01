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
        description="Run browser in headless mode.",
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
    max_retries: int = Field(
        default=5,
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
    # Worker pool
    # =====================
    workers: int = Field(
        default=2,
        alias="WORKERS",
        description="Number of Playwright worker processes.",
    )
    queue_maxsize: int = Field(
        default=200,
        alias="QUEUE_MAXSIZE",
        description="Maximum number of pending tasks in worker queue.",
    )
    job_timeout_ms: int = Field(
        default=45_000,
        alias="JOB_TIMEOUT_MS",
        description="Timeout for a single scraping job in milliseconds.",
    )
    jobs_enabled: bool = Field(
        default=True,
        alias="JOBS_ENABLED",
        description="Enable background job runner for batch scraping.",
    )
    job_result_ttl_s: float = Field(
        default=600.0,
        ge=0,
        alias="JOB_RESULT_TTL_S",
        description=(
            "Seconds a completed job's result (raw_html + screenshots) is kept "
            "in memory after it finishes, then evicted by the GC sweep. 0 "
            "disables time-based eviction. Keep comfortably above how long a "
            "consumer takes to fetch /results."
        ),
    )
    job_result_max: int = Field(
        default=1000,
        ge=0,
        alias="JOB_RESULT_MAX",
        description=(
            "Hard ceiling on retained job records. On overflow the oldest "
            "finished jobs are evicted first; in-flight jobs are never dropped. "
            "0 disables the size cap."
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

    # =====================
    # Sessions (Phase 1)
    # =====================
    sessions_max: int = Field(
        default=256,
        alias="SESSIONS_MAX",
        description="Soft cap on simultaneous sessions per process. LRU-evicted on overflow.",
    )
    session_storage_state_max_bytes: int = Field(
        default=2 * 1024 * 1024,
        alias="SESSION_STORAGE_STATE_MAX_BYTES",
        description="Per-session storage_state byte cap. Rejected on overflow.",
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
