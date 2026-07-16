from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Literal

from playwright.async_api import async_playwright, Browser, BrowserContext, Error as PWError
from playwright_stealth import Stealth

from src.proxy.models import ProxyConfig
from src.proxy.socks_bridge import open_socks_to_http_bridge
from src.browser.geo_profile import resolve_profile
from src.settings import settings


log = logging.getLogger(__name__)
# playwright_stealth's default WebGL spoof reports a macOS renderer ("Intel Iris
# OpenGL Engine"), which contradicts our Windows UA/navigator.platform and is a
# cross-check that advanced anti-bots (CreepJS/Cloudflare) flag. Override it with
# a real Windows Chrome ANGLE/Direct3D renderer so the GPU identity matches the
# rest of the fingerprint. Desktop preset is Windows; the value suits it.
_stealth = Stealth(
    webgl_vendor_override="Google Inc. (Intel)",
    webgl_renderer_override=(
        "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"
    ),
    # These evasions patch navigator getters in JS, whose `.toString()` returns
    # non-native source — an integrity tell that anti-bots (e.g. ServicePipe)
    # probe directly. Disable them and supply the same values natively instead:
    #   - user_agent / platform: set via CDP Emulation.setUserAgentOverride below,
    #     which the engine applies at the protocol level (native getter preserved).
    #   - vendor / plugins / mimeTypes: headless Chromium already reports the real
    #     Chrome values ("Google Inc." + the PDF viewer plugins), so no spoof needed.
    #   - languages: Playwright's context `locale` sets navigator.languages natively.
    navigator_user_agent=False,
    navigator_platform=False,
    navigator_vendor=False,
    navigator_plugins=False,
    navigator_languages=False,
    # Clear the matching *_override values too: with the evasions off they are
    # unused, and leaving them set makes playwright_stealth warn on every start
    # ("override provided but evasion disabled") — misleading log noise.
    navigator_platform_override=None,
    navigator_languages_override=None,
)

# WebRTC leak-protection init script, kept as a standalone .js asset so it reads
# and edits as JavaScript (syntax highlighting, no Python-string escaping of the
# regex/backslashes). Loaded once at import; see the file for the full rationale.
_WEBRTC_STEALTH_JS = (Path(__file__).resolve().parent / "webrtc_stealth.js").read_text(
    encoding="utf-8"
)


# Desktop default viewport (and matching window.screen). 1920x1080 @ DPR 1 is
# the single most common real desktop resolution, so it blends in. Shared with
# the Camoufox path so both engines default to the same size.
DEFAULT_DESKTOP_VIEWPORT = {"width": 1920, "height": 1080}

DESKTOP = {
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "viewport": DEFAULT_DESKTOP_VIEWPORT,
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


def _chrome_ua_metadata(user_agent: str) -> dict | None:
    """Build coherent User-Agent Client Hints from a Chrome UA string.

    Playwright's UA override changes the UA *string* but leaves the browser's
    real ``userAgentMetadata`` — so on headless Chromium the ``Sec-CH-UA``
    header leaks ``"HeadlessChrome"`` and the real bundle version (verified on
    the wire), contradicting the spoofed UA. Feeding this metadata to
    ``Emulation.setUserAgentOverride`` aligns the brands/version with the UA and
    drops the ``HeadlessChrome`` tell. Returns None for non-Chrome UAs (e.g. the
    mobile Safari preset), which do not send Sec-CH-UA at all.
    """
    match = re.search(r"Chrome/(\d+)", user_agent)
    if not match:
        return None
    major = match.group(1)
    if "Windows" in user_agent:
        platform = "Windows"
    elif "Macintosh" in user_agent or "Mac OS X" in user_agent:
        platform = "macOS"
    elif "Android" in user_agent:
        platform = "Android"
    else:
        platform = "Linux"
    brands = [
        {"brand": "Chromium", "version": major},
        {"brand": "Google Chrome", "version": major},
        {"brand": "Not.A/Brand", "version": "24"},
    ]
    return {
        "brands": brands,
        "fullVersionList": [
            {"brand": b["brand"], "version": f"{b['version']}.0.0.0"} for b in brands
        ],
        "platform": platform,
        "platformVersion": "",
        "architecture": "x86",
        "bitness": "64",
        "model": "",
        "mobile": "Mobile" in user_agent or "Android" in user_agent,
    }


def _navigator_platform_for_ua(user_agent: str) -> str:
    """The `navigator.platform` string matching a UA's OS.

    Set natively via CDP (Emulation.setUserAgentOverride) instead of a stealth JS
    patch, so `navigator.platform`'s getter stays `[native code]`.
    """
    # Order matters: an iPhone UA carries "like Mac OS X", so the mobile checks
    # must run before the desktop-Mac check or they'd be misread as MacIntel.
    if "Windows" in user_agent:
        return "Win32"
    if "iPhone" in user_agent:
        return "iPhone"
    if "Android" in user_agent:
        return "Linux armv8l"
    if "Macintosh" in user_agent or "Mac OS X" in user_agent:
        return "MacIntel"
    return "Linux x86_64"


_CHROME_UA_VERSION_RE = re.compile(r"Chrome/\d+(?:\.\d+)*")


def _align_ua_to_engine(user_agent: str, browser_version: str) -> str:
    """Rewrite the UA's Chrome version to match the real engine build.

    The bundled Chromium (e.g. Playwright 1.57 ships Chrome-for-Testing 143)
    drifts from a hardcoded UA string; advertising an old major while exposing a
    newer engine's JS/CSS feature set is a bot tell. Chrome's own UA reduction
    pins everything but the major to zero, so we mirror that: ``Chrome/<major>.0.0.0``.
    Returns the UA unchanged if it carries no ``Chrome/`` token (non-Chrome UA)
    or the version can't be parsed.
    """
    if not isinstance(browser_version, str):
        return user_agent
    major = browser_version.split(".", 1)[0]
    if not major.isdigit() or not _CHROME_UA_VERSION_RE.search(user_agent):
        return user_agent
    return _CHROME_UA_VERSION_RE.sub(f"Chrome/{major}.0.0.0", user_agent)


# Element-screenshot timing/padding constants. Not in Settings — per-deploy
# variation has no realistic use case.
_ELEMENT_SCROLL_TIMEOUT_MS = 3000
_ELEMENT_NETWORKIDLE_TIMEOUT_MS = 2000
_ELEMENT_PADDING_PX = 24

# Resource types aborted when asset blocking is enabled.
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})


async def _block_assets_route(route) -> None:
    """Abort heavy asset requests, continue everything else.

    Must be awaited by Playwright (registered as a coroutine handler, not a
    fire-and-forget ``create_task``) so abort/continue settle before teardown
    and their errors are not swallowed.
    """
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


# Captcha / block interstitials are tiny (Google's /sorry page is ~3 KB); a
# rendered results or content page is far larger (a Google SERP is ~90 KB+).
# Above this ceiling we treat trigger phrases as page content, not a block.
_MAX_BLOCK_PAGE_BYTES = 20_000

# HTTP statuses that mean "this exit IP is burned" — the body (if any) is a
# block/error page, not target content, so the fetch is not ok and the queue
# should rotate to a fresh proxy. Mirrored by the queue's retry policy.
HTTP_BAN_STATUSES = frozenset({401, 403, 407, 429})
# Transient upstream/proxy failures: also not ok and worth a fresh exit, but
# the IP isn't necessarily burned (so not flagged as a hard "block").
HTTP_TRANSIENT_STATUSES = frozenset(
    {500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 413}
)


def classify_fetch(
    status_code: int | None, *, captcha_detected: bool
) -> tuple[bool, bool, str | None]:
    """Decide (ok, blocked, error) from the HTTP status + captcha heuristic.

    A navigation that returns a ban/transient HTTP status still yields an HTML
    body, so Playwright reports no error — but the response is not genuine
    content. Marking it ``ok=False`` lets the queue's retry loop reach its
    proxy-rotation decision instead of short-circuiting on ``ok``. A genuine
    target status (e.g. 404) stays ``ok`` so we don't waste proxies retrying it.
    """
    if captcha_detected:
        return False, True, "Captcha/block detected by heuristic"
    if status_code in HTTP_BAN_STATUSES:
        return False, True, f"HTTP {status_code} (proxy ban / rate limit)"
    if status_code in HTTP_TRANSIENT_STATUSES:
        return False, False, f"HTTP {status_code} (transient upstream failure)"
    return True, False, None


def looks_like_captcha_or_block(html: str, *, final_url: str | None = None) -> bool:
    """Heuristic for detecting captcha / block pages.

    Real captcha / challenge interstitials are *tiny* compared to a rendered
    content page, so size is the primary discriminator: above
    ``_MAX_BLOCK_PAGE_BYTES`` we treat the page as genuine content and ignore
    trigger phrases in it. Otherwise a real results page that merely *mentions*
    a phrase — e.g. a Google SERP for a query about captchas / anti-detect
    tooling — gets misread as a block, which fails the fetch and drives a
    wasteful chain of proxy-rotating retries.

    ``final_url`` is the one size-independent signal: Google serves real
    blocks from ``/sorry/`` (HTTP 200) and Yandex SmartCaptcha from
    ``/showcaptcha`` (HTTP 200), so the redirect target (not the status code)
    is what unambiguously marks the block. Without the Yandex marker a
    SmartCaptcha page is read as a successful empty fetch, so the queue never
    rotates past a captcha'd exit and the SERP parse returns zero results.

    Extracted to module level so the Camoufox runner can reuse it without
    duplication (the heuristic body must not be copied verbatim).
    """
    if final_url:
        final_lower = final_url.lower()
        if "/sorry/" in final_lower or "/showcaptcha" in final_lower:
            return True
    # Past the block-page size ceiling we're looking at real content; any
    # trigger phrase there (result snippets, help text, footers) is a false
    # positive, so don't inspect the body.
    if len(html) > _MAX_BLOCK_PAGE_BYTES:
        return False
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
    # The bare word "captcha" is the weakest signal — only trust it on a
    # block-sized page (the size ceiling above already bounds us here).
    if "captcha" in html_lower:
        return True
    return False


def warmup_origin(target_url: str) -> str | None:
    """scheme://host/ of the target, or None if not a usable absolute URL."""
    from urllib.parse import urlparse
    p = urlparse(target_url)
    if not p.scheme or not p.hostname:
        return None
    return f"{p.scheme}://{p.hostname}/"


async def run_warmup(page, target_url, warmup, *, timeout_ms, default_dwell_ms) -> dict | None:
    """Optional pre-navigation warmup. Non-fatal: any failure is logged and
    swallowed so the real navigation still runs.

    type='homepage' visits the target's own origin; type='custom' visits
    warmup['url'] verbatim. Both then dwell before the caller's real navigation.

    Returns a descriptor of what actually ran ({type, url, dwell_ms}, with `url`
    being the URL actually visited), or None when warmup was not configured, had
    no usable URL, or failed — so callers can report what was applied, not just
    what was requested.
    """
    if not warmup:
        return None
    wtype = warmup.get("type", "homepage")
    if wtype == "homepage":
        warm_url = warmup_origin(target_url)
    elif wtype == "custom":
        warm_url = warmup.get("url") or None
    else:
        return None
    if not warm_url:
        return None
    dwell = warmup.get("dwell_ms")
    dwell = default_dwell_ms if dwell is None else dwell
    try:
        await page.goto(str(warm_url), wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(dwell)
        return {"type": wtype, "url": str(warm_url), "dwell_ms": dwell}
    except Exception as exc:  # pylint: disable=broad-except
        log.warning("warmup failed (non-fatal) for %s: %s", warm_url, exc)
        return None


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
    # What the pre-navigation warmup actually did ({type, url, dwell_ms}), or
    # None if no warmup ran. Distinct from the requested warmup config.
    applied_warmup: dict | None = None
    storage_state: dict | None = None
    element_status: str | None = None
    # True when the page looked like a captcha / anti-bot block. Distinct from a
    # network/proxy error: the request succeeded but the IP is burned, so the
    # queue layer should rotate the proxy and retry.
    blocked: bool = False


class PlaywrightRunner:
    def __init__(self, headless: bool, block_assets: bool, timeout_ms: int, engine: str = "chromium"):
        self.headless = headless
        self.block_assets = block_assets
        self.timeout_ms = timeout_ms
        self._engine = engine
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
            #
            # Separately: hide the headless-automation signal. Without this flag
            # Google (and other anti-bot SERPs) redirect a headless Chromium
            # straight to a /sorry captcha even from a clean residential IP; with
            # it, the same browser renders a full SERP. Verified empirically.
            launch_args: list[str] = []
            if self._engine == "chromium":
                launch_args.append("--disable-blink-features=AutomationControlled")
                if settings.webrtc_block:
                    launch_args.extend([
                        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                        "--force-webrtc-ip-handling-policy",
                    ])
            browser_type = getattr(self._playwright, self._engine)
            launch_kwargs = {"headless": self.headless}
            # Drive a real Google Chrome (channel="chrome") instead of the bundled
            # Chromium when configured — real branding/codecs and a populated
            # navigator.plugins, and a newer engine. Chromium-only; the bundled
            # Firefox/WebKit/Camoufox have no such channel.
            if self._engine == "chromium" and settings.chrome_channel:
                launch_kwargs["channel"] = settings.chrome_channel
            # No launch-level proxy on any engine. Playwright honours a
            # per-context proxy (set in _new_context) for Firefox/WebKit without
            # a launch placeholder, and a no-proxy context goes direct. The old
            # {"server": "per-context"} placeholder leaked into no-proxy contexts
            # as a literal host and broke them (NS_ERROR_UNKNOWN_PROXY_HOST).
            if launch_args:
                launch_kwargs["args"] = launch_args
            self._browser = await browser_type.launch(**launch_kwargs)

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
        viewport: dict[str, int] | None = None,
    ) -> BrowserContext:
        assert self._browser is not None
        preset = DESKTOP if device == "desktop" else MOBILE
        # A caller-supplied viewport overrides the device preset. window.screen
        # is set to the same size (below) so screen and innerWidth stay
        # consistent — a window larger than the screen is a fingerprint tell.
        effective_viewport = viewport or preset["viewport"]

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

        # Advertise the real engine major in the UA (and, via _chrome_ua_metadata
        # below, in Sec-CH-UA) so we don't claim an old Chrome while exposing a
        # newer engine's features. Only meaningful on Chromium — Firefox/WebKit
        # keep the preset UA, and Camoufox owns its own UA.
        effective_ua = preset["user_agent"]
        if self._engine == "chromium":
            effective_ua = _align_ua_to_engine(effective_ua, self._browser.version)

        context = await self._browser.new_context(
            user_agent=effective_ua,
            viewport=effective_viewport,
            screen=effective_viewport,
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
        context._applied_user_agent = effective_ua  # type: ignore[attr-defined]
        context._applied_locale = locale  # type: ignore[attr-defined]
        context._applied_timezone = timezone_id  # type: ignore[attr-defined]
        context._applied_accept_language = (  # type: ignore[attr-defined]
            effective_headers.get("Accept-Language")
            or effective_headers.get("accept-language")
        )

        # WebRTC leak protection, applied at the JS level because the Chromium
        # launch flags aren't always effective in headless mode. The script is
        # kept in webrtc_stealth.js (loaded as _WEBRTC_STEALTH_JS); see that file
        # for why the API is preserved native-looking rather than deleted.
        if settings.webrtc_block:
            await context.add_init_script(_WEBRTC_STEALTH_JS)

        effective_block_assets = self.block_assets if block_assets is None else block_assets
        if effective_block_assets:
            await context.route("**/*", _block_assets_route)
        return context

    def _looks_like_captcha_or_block(
        self, html: str, *, final_url: str | None = None
    ) -> bool:
        """Delegate to the module-level function; kept for backward compat."""
        return looks_like_captcha_or_block(html, final_url=final_url)

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
        # Camoufox-only premium options — accepted here so the call shape in
        # scrape_runner.py is identical for both runners; ignored by Chromium/Firefox/WebKit.
        humanize: bool = False,
        spoof_os: str | None = None,
        block_webgl: bool = False,
        addons: list[str] | None = None,
        warmup: dict | None = None,
    ) -> FetchResult:
        await self.start()
        assert self._browser is not None

        if storage_state and cookies:
            log.warning("both storage_state and cookies provided — ignoring cookies")
            cookies = None

        effective_block_assets = self.block_assets if block_assets is None else block_assets

        effective_proxy, bridge_cm = await self.resolve_proxy(proxy)

        context = None
        page = None

        async def _teardown() -> None:
            if page is not None:
                with contextlib.suppress(Exception):
                    await page.close()
            if context is not None:
                with contextlib.suppress(Exception):
                    await context.close()
            if bridge_cm is not None:
                with contextlib.suppress(Exception):
                    await bridge_cm.__aexit__(None, None, None)

        # Acquire context/page outside the main fetch try; a failure here would
        # otherwise leak the context and SOCKS bridge (the fetch finally never
        # runs because its try is never entered).
        try:
            context = await self._new_context(
                device=device, proxy=effective_proxy, headers=headers,
                block_assets=block_assets, proxy_geo=proxy_geo, render=render,
                storage_state=storage_state, viewport=viewport,
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
            if stealth and self._engine == "chromium":
                await _stealth.apply_stealth_async(page)
            elif stealth:
                log.debug("stealth requested but skipped: not supported for engine=%r", self._engine)

            # Align Client Hints with the spoofed UA: Playwright's UA override
            # leaves the real headless metadata, so Sec-CH-UA otherwise leaks
            # "HeadlessChrome" + the real version. Chromium only; per-page CDP.
            # Non-fatal: any failure (incl. building the metadata) is swallowed.
            if self._engine == "chromium":
                try:
                    ua = getattr(context, "_applied_user_agent", None)
                    ua_metadata = _chrome_ua_metadata(ua) if isinstance(ua, str) else None
                    if ua_metadata is not None:
                        cdp = await context.new_cdp_session(page)
                        await cdp.send("Emulation.setUserAgentOverride", {
                            "userAgent": ua,
                            # Native navigator.platform (replaces the disabled
                            # stealth navigator_platform JS patch that leaked its
                            # source via toString).
                            "platform": _navigator_platform_for_ua(ua),
                            "userAgentMetadata": ua_metadata,
                        })
                except Exception as exc:  # pylint: disable=broad-except
                    # Anti-bot-relevant: a silent no-op re-leaks "HeadlessChrome"
                    # in Sec-CH-UA and gets scrapes blocked, so surface it.
                    log.warning("Client-Hints override failed (Sec-CH-UA may leak HeadlessChrome): %s", exc)
        except BaseException:
            await _teardown()
            raise

        applied = {
            "user_agent": getattr(context, "_applied_user_agent", None),
            "locale": getattr(context, "_applied_locale", None),
            "timezone": getattr(context, "_applied_timezone", None),
            "accept_language": getattr(context, "_applied_accept_language", None),
        }

        # Late-bound: set inside the try below, read by _with_applied when each
        # FetchResult is wrapped after the fetch completes.
        applied_warmup: dict | None = None

        def _with_applied(result: FetchResult) -> FetchResult:
            result.applied_user_agent = applied["user_agent"]
            result.applied_locale = applied["locale"]
            result.applied_timezone = applied["timezone"]
            result.applied_accept_language = applied["accept_language"]
            result.applied_warmup = applied_warmup
            return result

        try:
            effective_timeout_ms = timeout_ms or self.timeout_ms
            applied_warmup = await run_warmup(
                page, url, warmup,
                timeout_ms=effective_timeout_ms,
                default_dwell_ms=settings.warmup_dwell_ms,
            )
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
            final_url = page.url
            status_code = resp.status if resp is not None else None
            captcha_detected = self._looks_like_captcha_or_block(
                html, final_url=final_url
            )
            fetch_ok, fetch_blocked, fetch_error = classify_fetch(
                status_code, captcha_detected=captcha_detected
            )

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
                ok=fetch_ok,
                error=fetch_error,
                storage_state=new_storage_state,
                element_status=element_status,
                blocked=fetch_blocked,
            ))

        except PWError as e:
            return _with_applied(FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error=f"PlaywrightError: {str(e)}",
                element_status="no_screenshot",
            ))
        except Exception as e:
            return _with_applied(FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error=f"UnexpectedError: {str(e)}",
                element_status="no_screenshot",
            ))
        finally:
            await _teardown()
