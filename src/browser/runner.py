from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import Optional, Literal

from typing import Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Error as PWError

from src.proxy.models import ProxyConfig
from src.browser.errors import BlockedOrCaptchaError

log = logging.getLogger(__name__)


DESKTOP = {
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "viewport": {"width": 1280, "height": 800},
}
MOBILE = {
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
    "viewport": {"width": 390, "height": 844},
}


@dataclass
class FetchResult:
    html: str
    final_url: str | None
    status_code: int | None
    screenshot_b64: str | None
    ok: bool  # True if success
    error: str | None  # Error description, if set not ok


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
            self._browser = await self._playwright.chromium.launch(headless=self.headless)

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

        context = await self._browser.new_context(
            user_agent=preset["user_agent"],
            viewport=preset["viewport"],
            proxy=proxy_arg,
            extra_http_headers=headers or None,
        )

        if self.block_assets:
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
        html = html.lower()
        signals = [
            "captcha",
            "unusual traffic",
            "verify you are a human",
            "access denied",
            "temporarily blocked",
            "/sorry/",
        ]
        return any(signal in html for signal in signals)

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
    ) -> FetchResult:
        await self.start()
        assert self._browser is not None

        context = await self._new_context(device=device, proxy=proxy, headers=headers)
        page = await context.new_page()

        try:
            effective_timeout_ms = timeout_ms or self.timeout_ms
            resp = await page.goto(url, wait_until=wait_until, timeout=effective_timeout_ms)
            if wait_for_selector:
                await page.wait_for_selector(wait_for_selector, timeout=effective_timeout_ms)

            html = await page.content()

            # Check on captcha/block
            if self._looks_like_captcha_or_block(html):
                return FetchResult(
                    html=html,
                    final_url=page.url,
                    status_code=resp.status if resp else None,
                    screenshot_b64=None,
                    ok=False,
                    error="Captcha/block detected by heuristic"
                )

            final_url = page.url
            status_code = resp.status if resp is not None else None

            screenshot_b64 = None
            if screenshot:
                screenshot_png = await page.screenshot(full_page=True)
                screenshot_b64 = base64.b64encode(screenshot_png).decode("ascii")

            return FetchResult(
                html=html,
                final_url=final_url,
                status_code=status_code,
                screenshot_b64=screenshot_b64,
                ok=True,
                error=None
            )

        except BlockedOrCaptchaError as e:
            # Catch blocking specific error
            return FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error=f"BlockedOrCaptcha: {str(e)}"
            )
        except PWError as e:
            # Playwright errors (timeouts, network problems and so on)
            return FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error=f"PlaywrightError: {str(e)}"
            )
        except Exception as e:
            # Any other errors
            return FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error=f"UnexpectedError: {str(e)}"
            )
        finally:
            await page.close()
            await context.close()
