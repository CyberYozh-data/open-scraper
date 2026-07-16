"""Per-request Camoufox runner.

Camoufox (anti-detect Firefox fork) is the only engine that beats Yandex
SmartCaptcha. Its fingerprint rotates per launch, so we launch a fresh
instance per request and tear it down — strongest anti-detect posture and
zero idle RAM. Mirrors PlaywrightRunner.fetch's result shape so the queue
layer stays engine-agnostic.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Literal, Optional

from camoufox.async_api import AsyncCamoufox

from src.browser.runner import (
    DEFAULT_DESKTOP_VIEWPORT,
    FetchResult,
    _capture_screenshot,
    classify_fetch,
    looks_like_captcha_or_block,
    run_warmup,
)
from src.proxy.models import ProxyConfig
from src.settings import settings

log = logging.getLogger(__name__)


def build_camoufox_options(
    *,
    proxy: dict | None,
    block_assets: bool,
    webrtc_block: bool,
    humanize: bool = False,
    spoof_os: str | None = None,
    block_webgl: bool = False,
    addons: list | None = None,
    viewport: dict[str, int] | None = None,
) -> dict:
    """Build the keyword-argument dict passed to AsyncCamoufox().

    Only non-default / non-None values that Camoufox actually accepts are
    included. ``geoip=True`` is always set so the browser locale/timezone
    aligns with the exit IP when a proxy is provided.

    ``viewport`` is forwarded as Camoufox's ``window=(w, h)`` outer size;
    Camoufox then derives a consistent screen (>= window), which fixes the
    otherwise-random screen that could be smaller than the window (a tell).
    """
    opts: dict = {
        "headless": settings.headless,
        "geoip": True,
        "block_images": bool(block_assets),
        "block_webrtc": bool(webrtc_block),
        "humanize": bool(humanize),
        "block_webgl": bool(block_webgl),
    }
    if proxy is not None:
        opts["proxy"] = proxy
    if spoof_os is not None:
        opts["os"] = spoof_os
    if addons is not None:
        opts["addons"] = addons
    if viewport is not None:
        opts["window"] = (viewport["width"], viewport["height"])
    return opts


class CamoufoxRunner:
    """Per-request Camoufox browser runner.

    Unlike PlaywrightRunner (which keeps a warm browser across requests),
    CamoufoxRunner launches a fresh browser for every fetch and tears it down
    immediately after. This means is_started() is always False; start() and
    stop() are no-ops so the worker can treat runners uniformly.
    """

    def __init__(self, timeout_ms: int) -> None:
        self._timeout_ms = timeout_ms

    def is_started(self) -> bool:
        """Always False — Camoufox launches per request, nothing stays warm."""
        return False

    async def start(self) -> None:
        """No-op: Camoufox has nothing to pre-warm."""
        return None

    async def stop(self) -> None:
        """No-op: the browser is already torn down after each fetch."""
        return None

    async def resolve_proxy(
        self, proxy: Optional[ProxyConfig]
    ) -> tuple[Optional[ProxyConfig], None]:
        """Camoufox accepts plain HTTP/SOCKS proxies natively; no bridge needed."""
        return proxy, None

    async def fetch(
        self,
        url: str,
        device: str,
        proxy: Optional[ProxyConfig],
        headers: dict[str, str] | None,
        wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"],
        wait_for_selector: str | None,
        timeout_ms: int | None,
        screenshot: bool,
        element_selector: str | None = None,
        stealth: bool = True,
        block_assets: bool | None = None,
        proxy_geo: dict[str, str] | None = None,
        render: bool = True,
        cookies: list[dict[str, Any]] | None = None,
        storage_state: dict | None = None,
        viewport: dict[str, int] | None = None,
        # Camoufox premium options
        humanize: bool = False,
        spoof_os: str | None = None,
        block_webgl: bool = False,
        addons: list | None = None,
        warmup: dict | None = None,
    ) -> FetchResult:
        """Fetch a URL using a fresh Camoufox instance.

        screenshot / element_selector are honoured: a Camoufox page is a
        Playwright (Firefox) page, so the shared `_capture_screenshot` helper
        applies (full-page or element crop). meta.applied_* is read back from
        the page so the locale/timezone Camoufox aligned via geoip are surfaced.

        headers are applied via page.set_extra_http_headers(), except
        User-Agent, which Camoufox owns as part of its fingerprint. An
        explicit Accept-Language overrides the geoip-derived one on the
        wire (parity with the Playwright path, where the user's value
        wins), but navigator.languages keeps the fingerprint value — a
        detectable mismatch, so override it only deliberately.

        Accepted-and-ignored params (v1, no crash):
          - cookies: Camoufox context API differs; cookie injection deferred.
          - stealth / device: Camoufox manages its own fingerprint internally.
          - proxy_geo / render: informational only for Playwright; geoip=True
            handles geo alignment for Camoufox.
          - storage_state: session reuse deferred to a future task.
        """
        proxy_dict: dict | None = None
        if proxy is not None:
            proxy_dict = {"server": proxy.server}
            if proxy.username:
                proxy_dict["username"] = proxy.username
            if proxy.password:
                proxy_dict["password"] = proxy.password

        effective_block_assets = (
            settings.block_assets if block_assets is None else block_assets
        )
        effective_timeout_ms = timeout_ms or self._timeout_ms

        # Camoufox is desktop-only (mobile is rejected upstream), so default to
        # the shared desktop viewport when unset. Passing an explicit window
        # makes Camoufox derive a consistent screen (>= window) instead of a
        # random one that can end up smaller than the window (a fingerprint tell).
        opts = build_camoufox_options(
            proxy=proxy_dict,
            block_assets=effective_block_assets,
            webrtc_block=settings.webrtc_block,
            humanize=humanize,
            spoof_os=spoof_os,
            block_webgl=block_webgl,
            addons=addons,
            viewport=viewport or DEFAULT_DESKTOP_VIEWPORT,
        )

        # UA is fingerprint-owned and filtered out; an explicit Accept-Language
        # wins on the wire and is echoed via applied_* (see docstring).
        safe_headers = {
            k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"
        }
        if len(safe_headers) < len(headers or {}):
            log.warning(
                "camoufox: dropping User-Agent header override for %s "
                "(the fingerprint manages it)", url,
            )
        al_override = next(
            (v for k, v in safe_headers.items() if k.lower() == "accept-language"),
            None,
        )

        applied_warmup: dict | None = None
        try:
            async with AsyncCamoufox(**opts) as browser:
                page = await browser.new_page()
                if safe_headers:
                    await page.set_extra_http_headers(safe_headers)
                applied_warmup = await run_warmup(
                    page, url, warmup,
                    timeout_ms=effective_timeout_ms,
                    default_dwell_ms=settings.warmup_dwell_ms,
                )
                resp = await page.goto(
                    url, wait_until=wait_until, timeout=effective_timeout_ms
                )
                if wait_for_selector:
                    await page.wait_for_selector(
                        wait_for_selector, timeout=effective_timeout_ms
                    )
                html = await page.content()
                final_url = page.url
                status_code = resp.status if resp is not None else None
                captcha_detected = looks_like_captcha_or_block(html, final_url=final_url)
                fetch_ok, fetch_blocked, fetch_error = classify_fetch(
                    status_code, captcha_detected=captcha_detected
                )

                # Screenshot: a Camoufox page is a Playwright (Firefox) page, so
                # reuse the runner's capture helper. It never raises; the extra
                # guard only covers a non-Playwright mock or driver hiccup so a
                # capture failure can't turn a good fetch into an error.
                screenshot_b64 = None
                element_status = "no_screenshot"  # parity with PlaywrightRunner on capture failure
                try:
                    png, element_status = await _capture_screenshot(
                        page,
                        screenshot=screenshot,
                        element_selector=element_selector,
                        effective_block_assets=effective_block_assets,
                    )
                    if png is not None:
                        screenshot_b64 = base64.b64encode(png).decode("ascii")
                except Exception as exc:  # pylint: disable=broad-except
                    log.warning("Camoufox screenshot capture failed: %s", exc)

                # Read back the fingerprint Camoufox actually applied. geoip=True
                # aligns locale/timezone to the exit IP internally, but nothing
                # surfaced it before, so meta.applied_* came back null. Best
                # effort: a restricted/blocked page must not fail the fetch.
                applied: dict = {}
                try:
                    applied = await page.evaluate(
                        "() => ({"
                        "ua: navigator.userAgent,"
                        "locale: navigator.language,"
                        "tz: Intl.DateTimeFormat().resolvedOptions().timeZone,"
                        "al: (navigator.languages || []).join(',')"
                        "})"
                    ) or {}
                except Exception as exc:  # pylint: disable=broad-except
                    log.debug("Camoufox applied-meta readback failed: %s", exc)

                return FetchResult(
                    html=html,
                    final_url=final_url,
                    status_code=status_code,
                    screenshot_b64=screenshot_b64,
                    ok=fetch_ok,
                    error=fetch_error,
                    blocked=fetch_blocked,
                    element_status=element_status,
                    applied_user_agent=applied.get("ua"),
                    applied_locale=applied.get("locale"),
                    applied_timezone=applied.get("tz"),
                    applied_accept_language=al_override or applied.get("al"),
                    applied_warmup=applied_warmup,
                )
        except Exception as exc:  # pylint: disable=broad-except
            error_type = type(exc).__name__
            log.warning("CamoufoxRunner.fetch failed for %s: %s: %s", url, error_type, exc)
            return FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error=f"{error_type}: {exc}",
                element_status="no_screenshot",
                applied_warmup=applied_warmup,
            )
