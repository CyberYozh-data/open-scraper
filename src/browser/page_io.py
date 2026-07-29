"""Page-reading helpers shared by the Playwright and Camoufox runners.

Kept out of `runner.py` so the Camoufox path can reuse them without
importing the whole Chromium runner module.
"""
from __future__ import annotations

import contextlib

from playwright.async_api import Error as PWError


async def read_content_settling_navigation(page) -> str:
    """`page.content()` retried across in-flight navigations.

    Some targets keep navigating after domcontentloaded — SPA auth gates, and
    any interstitial that bounces through a challenge URL — so a single call
    races the redirect and raises. Shared by both runners: the Camoufox path
    reaches this while the page is mid-redirect to a captcha, where failing
    here would lose the very page the block classifier needs.
    """
    last_error: PWError | None = None
    for _ in range(4):
        try:
            return await page.content()
        except PWError as exc:
            last_error = exc
            msg = str(exc).lower()
            if "navigating" in msg or "changing the content" in msg:
                with contextlib.suppress(PWError):
                    await page.wait_for_load_state("domcontentloaded", timeout=3000)
                continue
            raise
    raise last_error
