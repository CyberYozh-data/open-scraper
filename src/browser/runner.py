from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Optional, Literal

from playwright.async_api import async_playwright, Browser, BrowserContext, Error as PWError
from playwright_stealth import Stealth

from src.proxy.models import ProxyConfig
from src.proxy.socks_bridge import open_socks_to_http_bridge
from src.browser.geo_profile import resolve_profile
from src.settings import settings


log = logging.getLogger(__name__)
_stealth = Stealth()


DESKTOP = {
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "viewport": {"width": 1280, "height": 800},
    "locale": "en-US",
    "timezone_id": "America/New_York",
    "color_scheme": "light",
}
MOBILE = {
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "viewport": {"width": 390, "height": 844},
    "locale": "en-US",
    "timezone_id": "America/New_York",
    "color_scheme": "light",
    "is_mobile": True,
    "has_touch": True,
}

# Element-screenshot timing/padding constants. Not in Settings — per-deploy
# variation has no realistic use case.
_ELEMENT_SCROLL_TIMEOUT_MS = 3000
_ELEMENT_NETWORKIDLE_TIMEOUT_MS = 2000
_ELEMENT_PADDING_PX = 24


async def _fullpage_screenshot(page, *, effective_block_assets: bool) -> bytes:
    """Scroll-for-lazy-load (when assets aren't blocked) then full-page snap.

    Identical to the inline block that lived in `PlaywrightRunner.fetch` before
    the Phase D1 refactor.
    """
    if not effective_block_assets:
        try:
            await page.evaluate(
                """
                async () => {
                  const el = document.scrollingElement
                    || document.documentElement
                    || document.body;
                  if (!el) return;
                  const step = Math.max(200, window.innerHeight * 0.8);
                  let guard = 0;
                  while (
                    el.scrollTop + window.innerHeight < el.scrollHeight
                    && guard++ < 200
                  ) {
                    el.scrollBy(0, step);
                    await new Promise(r => setTimeout(r, 120));
                  }
                  await new Promise(r => setTimeout(r, 200));
                  window.scrollTo(0, 0);
                }
                """
            )
        except PWError:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except PWError:
            pass
    return await page.screenshot(full_page=True)


async def _compute_element_clip(locator) -> dict | None:
    """Document-relative clip rectangle for an element, with 24px padding.

    Uses getBoundingClientRect + window.scrollX/Y because `locator.bounding_box()`
    returns viewport-relative coordinates that are wrong for `position: fixed`
    or sticky-parent elements after scroll. Clamps to the document's scrollable
    bounds so Chromium does not reject the clip rectangle.

    Returns None when the element has zero size or the evaluate fails.
    """
    try:
        geom = await locator.evaluate(
            """el => {
                const r = el.getBoundingClientRect();
                return {
                    x: r.left + window.scrollX,
                    y: r.top + window.scrollY,
                    w: r.width,
                    h: r.height,
                    docW: document.documentElement.scrollWidth,
                    docH: document.documentElement.scrollHeight,
                };
            }"""
        )
    except PWError:
        return None
    if not geom:
        return None
    # Defense-in-depth: a non-numeric or non-finite coordinate (NaN/Infinity)
    # would slip past the `<= 0` guards (NaN comparisons are always False) and
    # later crash the Playwright driver when serialized into the clip rect as
    # invalid JSON. Real Chromium clamps getBoundingClientRect to finite floats,
    # so this is unreachable in practice — but it keeps the helper's "never
    # raises" contract intact for any pathological page. Must run before the
    # `<= 0` comparison, which would itself raise on a non-numeric value.
    if not all(
        isinstance(geom.get(k), (int, float)) and math.isfinite(geom[k])
        for k in ("x", "y", "w", "h", "docW", "docH")
    ):
        return None
    if geom["w"] <= 0 or geom["h"] <= 0:
        return None
    padding = _ELEMENT_PADDING_PX
    x = max(0.0, geom["x"] - padding)
    y = max(0.0, geom["y"] - padding)
    right = min(geom["docW"], geom["x"] + geom["w"] + padding)
    bottom = min(geom["docH"], geom["y"] + geom["h"] + padding)
    width = right - x
    height = bottom - y
    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


async def _capture_screenshot(
    page,
    *,
    screenshot: bool,
    element_selector: str | None,
    effective_block_assets: bool,
) -> tuple[bytes | None, str]:
    """Return (png_bytes, status) for the requested screenshot mode.

    Never raises — every documented failure resolves into a returned status.
    Status strings match `src.schemas.ElementScreenshotStatus`.
    """
    if not screenshot:
        return (None, "no_screenshot")

    if not element_selector:
        try:
            png = await _fullpage_screenshot(
                page, effective_block_assets=effective_block_assets,
            )
        except PWError:
            return (None, "no_screenshot")
        return (png, "not_requested")

    # Element branch.
    _elem_start = time.monotonic_ns()
    fallback_status: str
    try:
        element_locator = page.locator(element_selector)
        count = await element_locator.count()
    except PWError:
        fallback_status = "fallback_invalid"
    else:
        if count == 0:
            fallback_status = "fallback_not_found"
        else:
            locator = element_locator.first
            try:
                await locator.scroll_into_view_if_needed(
                    timeout=_ELEMENT_SCROLL_TIMEOUT_MS,
                )
            except PWError:
                # A visibility/scroll timeout raises playwright TimeoutError,
                # a subclass of PWError; both land here.
                fallback_status = "fallback_timeout"
            else:
                try:
                    await page.wait_for_load_state(
                        "networkidle",
                        timeout=_ELEMENT_NETWORKIDLE_TIMEOUT_MS,
                    )
                except PWError:
                    pass
                clip = await _compute_element_clip(locator)
                if clip is None:
                    fallback_status = "fallback_zero_size"
                else:
                    try:
                        png = await page.screenshot(full_page=True, clip=clip)
                    except PWError:
                        fallback_status = "fallback_zero_size"
                    else:
                        log.info(
                            "element_capture: selector=%r status=%s elapsed_ms=%d",
                            element_selector[:200], "element",
                            (time.monotonic_ns() - _elem_start) // 1_000_000,
                        )
                        return (png, "element")

    # Fallback path: full-page (existing behaviour). If the fallback capture
    # itself raises, return no_screenshot so the response is internally
    # consistent.
    try:
        png = await _fullpage_screenshot(
            page, effective_block_assets=effective_block_assets,
        )
    except PWError:
        log.warning(
            "element_capture: fallback screenshot also failed for selector=%r status=%s",
            element_selector[:200], fallback_status,
        )
        return (None, "no_screenshot")
    log.info(
        "element_capture: selector=%r status=%s elapsed_ms=%d",
        element_selector[:200], fallback_status,
        (time.monotonic_ns() - _elem_start) // 1_000_000,
    )
    return (png, fallback_status)


@dataclass
class FetchResult:
    html: str
    final_url: str | None
    status_code: int | None
    screenshot_b64: str | None
    ok: bool  # True if success
    error: str | None  # Error description, if set not ok
    # Browser fingerprint that was actually applied to the page.
    applied_user_agent: str | None = None
    applied_locale: str | None = None
    applied_timezone: str | None = None
    applied_accept_language: str | None = None
    storage_state: dict | None = None
    element_status: str | None = None


class PlaywrightRunner:
    def __init__(self, headless: bool, block_assets: bool, timeout_ms: int):
        self.headless = headless
        self.block_assets = block_assets
        self.timeout_ms = timeout_ms
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            self._playwright = await async_playwright().start()
            # Optional WebRTC leak protection. When Chromium initialises
            # WebRTC it issues STUN requests over UDP directly to the remote
            # server, bypassing SOCKS5/HTTP proxies (which typically carry
            # TCP only). These flags force WebRTC to only use the proxied
            # path. Disable via WEBRTC_BLOCK=false if the scraped site
            # actually needs working WebRTC (video chat, RTCPeerConnection).
            launch_args: list[str] = []
            if settings.webrtc_block:
                launch_args.extend([
                    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--force-webrtc-ip-handling-policy",
                ])
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=launch_args or None,
            )

    async def stop(self) -> None:
        # Must only be called from the worker's own coroutine (between jobs):
        # the lock is held across browser.close(), so an outside watchdog
        # invoking stop() while a fetch is in flight would deadlock against
        # the start() call that fetch performs internally.
        async with self._lock:
            browser, self._browser = self._browser, None
            playwright, self._playwright = self._playwright, None
            # Always null the handles first so that even if close() raises
            # (wedged Chromium, broken WS pipe) the next start() will rebuild
            # the runner from scratch instead of reusing a dead Browser.
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()

    def is_started(self) -> bool:
        return self._browser is not None

    async def resolve_proxy(self, proxy: Optional[ProxyConfig]):
        """Translate a possibly-authenticated SOCKS5 proxy into a Playwright-safe one.

        Chromium does not support SOCKS5 with username/password. When the caller
        hands us an authenticated socks5:// URL we spawn a local HTTP-to-SOCKS5
        bridge and hand Playwright the local HTTP URL instead. Callers must
        ``await bridge_cm.__aexit__(None, None, None)`` in their finally clause
        when the returned bridge is not None.

        Returns:
            (effective_proxy, bridge_cm) — pass-through when no bridge is needed.
        """
        if proxy is None:
            return None, None
        needs_bridge = (
            proxy.server.lower().startswith("socks5://")
            and (proxy.username or proxy.password)
        )
        if not needs_bridge:
            return proxy, None

        from urllib.parse import urlparse, quote
        parsed = urlparse(proxy.server)
        auth = ""
        if proxy.username:
            auth = quote(proxy.username, safe="")
            if proxy.password:
                auth += ":" + quote(proxy.password, safe="")
            auth += "@"
        socks_url = f"socks5://{auth}{parsed.hostname}:{parsed.port}"
        bridge_cm = open_socks_to_http_bridge(socks_url)
        local_url = await bridge_cm.__aenter__()
        log.info("routing SOCKS5 proxy %s via local bridge %s", proxy.server, local_url)
        return ProxyConfig(server=local_url, username=None, password=None), bridge_cm

    async def _new_context(
        self,
        device: str,
        proxy: Optional[ProxyConfig],
        headers: dict[str, str] | None,
        block_assets: bool | None = None,
        proxy_geo: dict[str, str] | None = None,
        render: bool = True,
        storage_state: dict | None = None,
    ) -> BrowserContext:
        assert self._browser is not None
        preset = DESKTOP if device == "desktop" else MOBILE

        proxy_arg = None
        if proxy is not None:
            proxy_arg = {"server": proxy.server}
            if proxy.username:
                proxy_arg["username"] = proxy.username
            if proxy.password:
                proxy_arg["password"] = proxy.password

        # Align locale / timezone / Accept-Language with the proxy's geo so
        # the browser fingerprint matches the exit IP. Explicit Accept-Language
        # supplied via `headers` wins over the geo-derived default.
        locale = preset.get("locale", "en-US")
        timezone_id = preset.get("timezone_id", "America/New_York")
        accept_language: str | None = None

        if proxy_geo:
            profile = resolve_profile(proxy_geo.get("country_code"), proxy_geo.get("city"))
            if profile is not None:
                locale = profile.locale
                timezone_id = profile.timezone_id
                accept_language = profile.accept_language
                log.info(
                    "geo profile applied: country=%s city=%s -> locale=%s tz=%s",
                    proxy_geo.get("country_code"),
                    proxy_geo.get("city"),
                    locale,
                    timezone_id,
                )

        effective_headers = dict(headers) if headers else {}
        if accept_language and not any(k.lower() == "accept-language" for k in effective_headers):
            effective_headers["Accept-Language"] = accept_language

        context = await self._browser.new_context(
            user_agent=preset["user_agent"],
            viewport=preset["viewport"],
            locale=locale,
            timezone_id=timezone_id,
            color_scheme=preset.get("color_scheme", "light"),
            is_mobile=preset.get("is_mobile", False),
            has_touch=preset.get("has_touch", False),
            java_script_enabled=render,
            proxy=proxy_arg,
            extra_http_headers=effective_headers or None,
            storage_state=storage_state,
        )
        # Stash applied fingerprint on the context so fetch() can surface it
        # in FetchResult without needing to re-derive the values.
        context._applied_user_agent = preset["user_agent"]  # type: ignore[attr-defined]
        context._applied_locale = locale  # type: ignore[attr-defined]
        context._applied_timezone = timezone_id  # type: ignore[attr-defined]
        context._applied_accept_language = (  # type: ignore[attr-defined]
            effective_headers.get("Accept-Language")
            or effective_headers.get("accept-language")
        )

        # WebRTC leak protection. The Chromium launch flags above are not
        # always effective in headless mode, so also neutralise the WebRTC
        # APIs at the JS level before any page script runs.
        if settings.webrtc_block:
            await context.add_init_script("""
                (() => {
                  const kill = (name) => {
                    try {
                      Object.defineProperty(window, name, {
                        value: undefined,
                        writable: false,
                        configurable: false,
                      });
                    } catch (_) {}
                  };
                  kill('RTCPeerConnection');
                  kill('webkitRTCPeerConnection');
                  kill('mozRTCPeerConnection');
                  kill('RTCDataChannel');
                  kill('RTCSessionDescription');
                  kill('RTCIceCandidate');
                  if (navigator.mediaDevices) {
                    try {
                      navigator.mediaDevices.getUserMedia = () =>
                        Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));
                    } catch (_) {}
                  }
                })();
            """)

        effective_block_assets = self.block_assets if block_assets is None else block_assets
        if effective_block_assets:
            await context.route(
                "**/*",
                lambda route: asyncio.create_task(
                    route.abort()
                    if route.request.resource_type in {"image", "media", "font"}
                    else route.continue_()
                ),
            )
        return context

    def _looks_like_captcha_or_block(self, html: str) -> bool:
        """
        Heuristic for detecting captcha / block pages.

        Uses strong phrases only — a bare "captcha" word produces false positives
        on sites that merely mention it in their help / footer links. We also
        require the HTML to be relatively short, because block pages are
        typically tiny compared to real content pages.
        """
        html_lower = html.lower()
        strong_signals = (
            "unusual traffic",
            "verify you are a human",
            "verify you are human",
            "access denied",
            "temporarily blocked",
            "are you a robot",
            "enable javascript and cookies to continue",
            "/sorry/",
            "cf-chl-",  # cloudflare challenge marker
            "data-sitekey",  # reCAPTCHA / Turnstile attribute
        )
        if any(signal in html_lower for signal in strong_signals):
            return True
        # The bare word "captcha" is only a signal on small pages (real blocks
        # are tiny; content pages that just reference the word are huge).
        if "captcha" in html_lower and len(html) < 8000:
            return True
        return False

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
    ) -> FetchResult:
        await self.start()
        assert self._browser is not None

        if storage_state and cookies:
            log.warning("both storage_state and cookies provided — ignoring cookies")
            cookies = None

        effective_block_assets = self.block_assets if block_assets is None else block_assets

        effective_proxy, bridge_cm = await self.resolve_proxy(proxy)

        context = await self._new_context(
            device=device, proxy=effective_proxy, headers=headers,
            block_assets=block_assets, proxy_geo=proxy_geo, render=render,
            storage_state=storage_state,
        )

        if cookies:
            # Playwright requires either url or both domain+path. If the caller
            # omitted domain, default it to the URL's hostname and path="/".
            from urllib.parse import urlparse  # local import to avoid top-level pollution
            default_domain = urlparse(url).hostname or ""
            prepared_cookies = []
            for cookie in cookies:
                cookie_dict = dict(cookie)
                if not cookie_dict.get("domain") and not cookie_dict.get("url"):
                    cookie_dict["domain"] = default_domain
                    cookie_dict.setdefault("path", "/")
                prepared_cookies.append(cookie_dict)
            try:
                await context.add_cookies(prepared_cookies)
            except Exception as exc:  # pylint: disable=broad-except
                log.warning("failed to add cookies: %s", exc)

        page = await context.new_page()
        if stealth:
            await _stealth.apply_stealth_async(page)

        applied = {
            "user_agent": getattr(context, "_applied_user_agent", None),
            "locale": getattr(context, "_applied_locale", None),
            "timezone": getattr(context, "_applied_timezone", None),
            "accept_language": getattr(context, "_applied_accept_language", None),
        }

        def _with_applied(result: FetchResult) -> FetchResult:
            result.applied_user_agent = applied["user_agent"]
            result.applied_locale = applied["locale"]
            result.applied_timezone = applied["timezone"]
            result.applied_accept_language = applied["accept_language"]
            return result

        try:
            effective_timeout_ms = timeout_ms or self.timeout_ms
            resp = await page.goto(url, wait_until=wait_until, timeout=effective_timeout_ms)
            if wait_for_selector:
                await page.wait_for_selector(wait_for_selector, timeout=effective_timeout_ms)

            # Some sites (LinkedIn, SPA auth gates) keep navigating after
            # domcontentloaded, so page.content() can race with a redirect.
            # Retry a few times, optionally waiting for the DOM to settle.
            html = ""
            last_error: PWError | None = None
            for _ in range(4):
                try:
                    html = await page.content()
                    last_error = None
                    break
                except PWError as exc:
                    last_error = exc
                    msg = str(exc).lower()
                    if "navigating" in msg or "changing the content" in msg:
                        try:
                            await page.wait_for_load_state(
                                "domcontentloaded", timeout=3000,
                            )
                        except PWError:
                            pass
                        continue
                    raise
            if last_error is not None:
                raise last_error
            captcha_detected = self._looks_like_captcha_or_block(html)

            final_url = page.url
            status_code = resp.status if resp is not None else None

            screenshot_b64 = None
            png, element_status = await _capture_screenshot(
                page,
                screenshot=screenshot,
                element_selector=element_selector,
                effective_block_assets=effective_block_assets,
            )
            if png is not None:
                screenshot_b64 = base64.b64encode(png).decode("ascii")

            new_storage_state = None
            if storage_state is not None:
                try:
                    new_storage_state = await context.storage_state()
                except PWError as exc:
                    log.warning("failed to capture storage_state: %s", exc)

            return _with_applied(FetchResult(
                html=html,
                final_url=final_url,
                status_code=status_code,
                screenshot_b64=screenshot_b64,
                ok=not captcha_detected,
                error="Captcha/block detected by heuristic" if captcha_detected else None,
                storage_state=new_storage_state,
                element_status=element_status,
            ))

        except PWError as e:
            return _with_applied(FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error=f"PlaywrightError: {str(e)}"
            ))
        except Exception as e:
            return _with_applied(FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error=f"UnexpectedError: {str(e)}"
            ))
        finally:
            await page.close()
            await context.close()
            if bridge_cm is not None:
                with contextlib.suppress(Exception):
                    await bridge_cm.__aexit__(None, None, None)
