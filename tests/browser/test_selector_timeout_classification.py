"""A `wait_for_selector` deadline must not hide why the page never rendered.

Both runners awaited `wait_for_selector` between `page.goto` and the capture
block, so a timeout there jumped straight to the `except PWError` handler. The
handler returns an empty FetchResult — no html, no final_url, no status — which
means `looks_like_captcha_or_block` never ran, even though it already
recognises the exact interstitials involved (`/sorry/`, `/showcaptcha`).

Observed in production 2026-07-26: yandex_search runs against the am/kz locales
were redirected to `https://yandex.ru/showcaptcha?...`, where the preset's
`li.serp-item` can never appear. Each attempt burned the full selector timeout
and surfaced as `page task exceeded 120.0s` — a captcha reported as a timeout,
4 runs out of 4. The queue cannot rotate past an exit it was never told was
blocked.

The selector timeout still fails the fetch — silently accepting a page that
never rendered its results is what produced truncated SERPs — but it now fails
with the page in hand, so the block classifier decides what kind of failure it
was.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeoutError

from src.browser.camoufox_runner import CamoufoxRunner
from src.browser.runner import PlaywrightRunner
from src.queue.scrape_runner import is_selector_miss

CAPTCHA_HTML = "<html><body>Подтвердите, что запросы отправляли вы</body></html>"
CAPTCHA_URL = "https://yandex.ru/showcaptcha?cc=1&form-fb-hint=1.1&mt=BF5E8009"
SERP_URL = "https://yandex.ru/search/?text=ноутбук&lr=159"


# The double must be the class Playwright actually raises. A bare PWError
# subclass would let an over-broad `except PWError` pass these tests, cementing
# the bug where a malformed selector is laundered into a "not found".
_Timeout = PWTimeoutError


def _page(*, content: str, url: str, status: int = 200, selector_times_out: bool = True):
    page = AsyncMock()
    page.goto = AsyncMock(return_value=Mock(status=status, request=Mock(redirected_from=None)))
    page.content = AsyncMock(return_value=content)
    page.url = url
    page.screenshot = AsyncMock(return_value=b"PNGBYTES")
    page.evaluate = AsyncMock(return_value={
        "ua": "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0",
        "locale": "ru", "tz": "Europe/Moscow", "al": "ru,en;q=0.9",
    })
    if selector_times_out:
        page.wait_for_selector = AsyncMock(
            side_effect=_Timeout(
                'Page.wait_for_selector: Timeout 45000ms exceeded.\n'
                'Call log:\n  - waiting for locator("li.serp-item") to be visible'
            )
        )
    else:
        page.wait_for_selector = AsyncMock()
    page.close = AsyncMock()
    return page


class _ACM:
    def __init__(self, browser):
        self._browser = browser

    async def __aenter__(self):
        return self._browser

    async def __aexit__(self, *a):
        return False


async def _playwright_fetch(page, **overrides):
    runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
    runner._browser = AsyncMock()
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    kwargs = dict(
        url=SERP_URL, device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector="li.serp-item",
        timeout_ms=30000, screenshot=False,
    )
    kwargs.update(overrides)
    with patch.object(runner, "_new_context", return_value=context):
        return await runner.fetch(**kwargs)


async def _camoufox_fetch(monkeypatch, page, **overrides):
    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)
    monkeypatch.setattr(
        "src.browser.camoufox_runner.AsyncCamoufox", lambda **kw: _ACM(browser)
    )
    kwargs = dict(
        url=SERP_URL, device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector="li.serp-item",
        timeout_ms=30000, screenshot=False,
    )
    kwargs.update(overrides)
    return await CamoufoxRunner(timeout_ms=30000).fetch(**kwargs)


class TestPlaywrightRunnerSelectorTimeout:
    @pytest.mark.asyncio
    async def test_captcha_redirect_is_reported_as_blocked_not_a_bare_timeout(self):
        res = await _playwright_fetch(_page(content=CAPTCHA_HTML, url=CAPTCHA_URL))
        assert res.blocked is True
        assert res.ok is False

    @pytest.mark.asyncio
    async def test_captcha_page_is_still_returned_for_diagnosis(self):
        res = await _playwright_fetch(_page(content=CAPTCHA_HTML, url=CAPTCHA_URL))
        assert res.html == CAPTCHA_HTML
        assert res.final_url == CAPTCHA_URL
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_block_reason_wins_over_the_selector_message(self):
        # Asserting only that the selector appears in the message would be
        # vacuous: Playwright's own timeout text already embeds the locator.
        # When the classifier knows WHY the selector never appeared, that
        # reason is the more actionable one and must survive.
        res = await _playwright_fetch(_page(content=CAPTCHA_HTML, url=CAPTCHA_URL))
        assert res.error == "Captcha/block detected by heuristic"

    @pytest.mark.asyncio
    async def test_unblocked_page_missing_the_selector_still_fails(self):
        """A rendered page that never grew the selector is incomplete, not OK.

        Yandex streamed only 2 of 18 result items in one live capture; accepting
        it as a success is how a truncated SERP reaches consumers looking clean.
        """
        res = await _playwright_fetch(
            _page(content="<html><body>partial</body></html>", url=SERP_URL)
        )
        assert res.ok is False
        assert res.blocked is False
        assert res.html == "<html><body>partial</body></html>"

    @pytest.mark.asyncio
    async def test_unexplained_miss_names_the_selector(self):
        res = await _playwright_fetch(
            _page(content="<html><body>partial</body></html>", url=SERP_URL)
        )
        assert res.error == "selector_not_found: li.serp-item"
        # Bind the two sides of the contract. Asserting the literal alone lets a
        # future change wrap the error and silently drop the queue back to
        # "no rotation" with the whole suite still green.
        assert is_selector_miss(res.error)

    @pytest.mark.asyncio
    async def test_malformed_selector_is_not_laundered_into_a_miss(self):
        """A selector the engine cannot parse raises a plain PWError, not a
        timeout. Reporting it as "not found" would misdiagnose it AND spend the
        full rotation budget on a request that can never succeed."""
        page = _page(content="<html><body>x</body></html>", url=SERP_URL)
        page.wait_for_selector = AsyncMock(
            side_effect=PWError(
                'Page.wait_for_selector: Unexpected token "" while parsing css selector'
            )
        )
        res = await _playwright_fetch(page, wait_for_selector="li.serp-item[")
        assert res.ok is False
        assert res.html == ""
        assert (res.error or "").startswith("PlaywrightError:")
        assert not is_selector_miss(res.error)

    @pytest.mark.asyncio
    async def test_successful_selector_wait_is_unchanged(self):
        res = await _playwright_fetch(
            _page(
                content="<html><body>real results</body></html>",
                url=SERP_URL,
                selector_times_out=False,
            )
        )
        assert res.ok is True
        assert res.blocked is False
        assert res.error is None

    @pytest.mark.asyncio
    async def test_goto_failure_is_still_a_plain_playwright_error(self):
        """Only the selector wait is recovered; a navigation that never landed
        has no page to classify, so it must keep failing as before."""
        page = _page(content=CAPTCHA_HTML, url=CAPTCHA_URL)
        page.goto = AsyncMock(side_effect=_Timeout("Page.goto: Timeout 30000ms exceeded."))
        res = await _playwright_fetch(page)
        assert res.ok is False
        assert res.blocked is False
        assert res.html == ""
        assert (res.error or "").startswith("PlaywrightError:")


class TestCamoufoxRunnerSelectorTimeout:
    """The Camoufox path is the one that actually shipped the yandex_search
    preset, so it carries the same guarantees."""

    @pytest.mark.asyncio
    async def test_captcha_redirect_is_reported_as_blocked(self, monkeypatch):
        res = await _camoufox_fetch(
            monkeypatch, _page(content=CAPTCHA_HTML, url=CAPTCHA_URL)
        )
        assert res.blocked is True
        assert res.ok is False
        assert res.final_url == CAPTCHA_URL

    @pytest.mark.asyncio
    async def test_block_reason_wins_over_the_selector_message(self, monkeypatch):
        res = await _camoufox_fetch(
            monkeypatch, _page(content=CAPTCHA_HTML, url=CAPTCHA_URL)
        )
        assert res.error == "Captcha/block detected by heuristic"

    @pytest.mark.asyncio
    async def test_unexplained_miss_names_the_selector(self, monkeypatch):
        res = await _camoufox_fetch(
            monkeypatch, _page(content="<html><body>partial</body></html>", url=SERP_URL)
        )
        assert res.ok is False
        assert res.error == "selector_not_found: li.serp-item"

    @pytest.mark.asyncio
    async def test_successful_selector_wait_is_unchanged(self, monkeypatch):
        res = await _camoufox_fetch(
            monkeypatch,
            _page(
                content="<html><body>real results</body></html>",
                url=SERP_URL,
                selector_times_out=False,
            ),
        )
        assert res.ok is True
        assert res.error is None
