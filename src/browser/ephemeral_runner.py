"""Throwaway PlaywrightRunner for a non-default launch mode.

A per-request headless/headful override must not add a second warm browser to
the worker's pool: a warm Chromium holds ~400-500 MB RSS while the per-worker
memory budget is ~1.25 GB, so pooling both modes would OOM the container. This
wrapper launches inside fetch() and tears down right after, mirroring
CamoufoxRunner's contract (is_started() always False, no-op start/stop) so the
registry and lifecycle loop can treat every runner uniformly.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.browser.runner import FetchResult, PlaywrightRunner
from src.proxy.models import ProxyConfig

log = logging.getLogger(__name__)


class EphemeralPlaywrightRunner:
    """PlaywrightRunner that launches per fetch and closes immediately after.

    Sequential use only: one in-flight fetch at a time (the worker runs with
    --max-async-tasks 1). Reuse across *sequential* attempts is fine and expected
    — the retry loop calls fetch() again on the same instance, each attempt
    getting a fresh browser. Concurrent fetches on one instance would share the
    single inner runner and tear each other's browser down."""

    def __init__(
        self, *, engine: str, headless: bool, block_assets: bool, timeout_ms: int
    ) -> None:
        self._inner = PlaywrightRunner(
            engine=engine,
            headless=headless,
            block_assets=block_assets,
            timeout_ms=timeout_ms,
        )

    def is_started(self) -> bool:
        """Always False — nothing stays warm, so the lifecycle loop skips it."""
        return False

    async def start(self) -> None:
        """No-op: the browser launches inside fetch()."""
        return None

    async def stop(self) -> None:
        """No-op: fetch() already closed the browser."""
        return None

    async def resolve_proxy(self, proxy: Optional[ProxyConfig]):
        """Delegate: proxy translation does not depend on a live browser."""
        return await self._inner.resolve_proxy(proxy)

    async def fetch(self, *args: Any, **kwargs: Any) -> FetchResult:
        # start() belongs inside the try: it brings the Playwright driver up
        # before launching the browser, so a failed launch would otherwise
        # leak the driver process — the very leak this class exists to avoid.
        # stop() is safe on a partially-started runner.
        try:
            await self._inner.start()
            return await self._inner.fetch(*args, **kwargs)
        finally:
            # Teardown is best-effort and must never replace the in-flight
            # exception: the fetch's error is what the worker reports, and
            # swapping in a teardown error would destroy the original diagnosis
            # at the worker boundary. CancelledError is a BaseException and so
            # still propagates (page_task_timeout_s cancels mid-fetch), while
            # the browser is closed either way.
            try:
                await self._inner.stop()
            except Exception:  # pylint: disable=broad-except
                log.warning("ephemeral browser teardown failed", exc_info=True)
