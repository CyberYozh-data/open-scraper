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
    jobs_worker_concurrency: int = Field(
        default=1,
        alias="JOBS_WORKER_CONCURRENCY",
        description="Number of concurrent background job processors.",
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
    # Debug
    # =====================
    debug_save_html: bool = Field(
        default=False,
        alias="DEBUG_SAVE_HTML",
        description="Save raw HTML to disk for debugging.",
    )
    debug_save_screenshot: bool = Field(
        default=False,
        alias="DEBUG_SAVE_SCREENSHOT",
        description="Save page screenshot to disk for debugging.",
    )
    out_dir: str = Field(
        default="out",
        alias="OUT_DIR",
        description="Directory where debug artifacts are saved.",
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
