from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
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
        async with self._lock:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self._browser = None
            self._playwright = None

    async def _new_context(
        self,
        device: str,
        proxy: Optional[ProxyConfig],
        headers: dict[str, str] | None,
        block_assets: bool | None = None,
        proxy_geo: dict[str, str] | None = None,
        render: bool = True,
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
        stealth: bool = True,
        block_assets: bool | None = None,
        proxy_geo: dict[str, str] | None = None,
        render: bool = True,
        cookies: list[dict[str, Any]] | None = None,
    ) -> FetchResult:
        await self.start()
        assert self._browser is not None

        effective_block_assets = self.block_assets if block_assets is None else block_assets

        # Chromium doesn't support SOCKS5 with authentication. If we are asked
        # to use an authenticated socks5:// proxy, spin up a local HTTP-to-
        # SOCKS5 bridge and hand Playwright the local HTTP URL instead.
        bridge_cm = None
        effective_proxy = proxy
        if proxy is not None and proxy.server.lower().startswith("socks5://") and (proxy.username or proxy.password):
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
            effective_proxy = ProxyConfig(server=local_url, username=None, password=None)

        context = await self._new_context(
            device=device, proxy=effective_proxy, headers=headers,
            block_assets=block_assets, proxy_geo=proxy_geo, render=render,
        )

        if cookies:
            # Playwright requires either url or both domain+path. If the caller
            # omitted domain, default it to the URL's hostname and path="/".
            from urllib.parse import urlparse  # local import to avoid top-level pollution
            default_domain = urlparse(url).hostname or ""
            prepared_cookies = []
            for cookie in cookies:
                c = dict(cookie)
                if not c.get("domain") and not c.get("url"):
                    c["domain"] = default_domain
                    c.setdefault("path", "/")
                prepared_cookies.append(c)
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
            last_err: PWError | None = None
            for _ in range(4):
                try:
                    html = await page.content()
                    last_err = None
                    break
                except PWError as exc:
                    last_err = exc
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
            if last_err is not None:
                raise last_err
            captcha_detected = self._looks_like_captcha_or_block(html)

            final_url = page.url
            status_code = resp.status if resp is not None else None

            screenshot_b64 = None
            if screenshot:
                # Only scroll-for-lazy-loading when assets are actually loaded —
                # otherwise it just wastes time on a page where images are blocked.
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
                        # Page may have navigated away or DOM is not ready —
                        # screenshot from whatever we have is still useful.
                        pass
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except PWError:
                        pass
                screenshot_png = await page.screenshot(full_page=True)
                screenshot_b64 = base64.b64encode(screenshot_png).decode("ascii")

            return _with_applied(FetchResult(
                html=html,
                final_url=final_url,
                status_code=status_code,
                screenshot_b64=screenshot_b64,
                ok=not captcha_detected,
                error="Captcha/block detected by heuristic" if captcha_detected else None,
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
