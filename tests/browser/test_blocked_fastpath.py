"""A blocked attempt must not sit out the selector deadline it can never meet.

Measured on yandex_search (timeout_ms=45000), six sequential attempts through
the real preset: five were blocked, and on every one of them `page.url` was
already https://yandex.ru/showcaptcha the moment `page.goto` returned — yet the
selector wait still ran to its full 45.0s before anything looked at the verdict.
The one unblocked attempt spent 0.0s there. So the verdict is available before
the wait, and the wait is pure loss: ~45s per blocked attempt, up to three
attempts per request.

The guard is deliberately narrow. See `redirected_to_block`.
"""
from __future__ import annotations

import pytest

from src.browser.runner import redirected_to_block


class TestRedirectedToBlock:
    """Only a redirect INTO a block endpoint counts."""

    def test_a_redirect_to_yandex_smartcaptcha_counts(self):
        assert redirected_to_block(
            "https://yandex.ru/search/?text=x",
            "https://yandex.ru/showcaptcha?cc=1&form-fb-hint=1.1",
        )

    def test_a_redirect_to_googles_sorry_counts(self):
        assert redirected_to_block(
            "https://www.google.com/search?q=x",
            "https://www.google.com/sorry/index?continue=https://www.google.com/",
        )

    def test_the_page_we_asked_for_never_counts(self):
        """The false positive that matters.

        `/sorry/` is a perfectly ordinary path — an apology page, a returns
        policy. A caller who navigated there ON PURPOSE has not been blocked,
        and skipping their selector wait would hand back a page that had not
        finished rendering. Only a URL we did not ask for can be a block.
        """
        assert not redirected_to_block(
            "https://shop.example/sorry/we-are-closed",
            "https://shop.example/sorry/we-are-closed",
        )

    def test_an_ordinary_redirect_does_not_count(self):
        assert not redirected_to_block(
            "https://yandex.ru/search/?text=x", "https://yandex.ru/search/?text=x&lr=213"
        )

    def test_no_current_url_does_not_count(self):
        assert not redirected_to_block("https://yandex.ru/search/?text=x", None)
        assert not redirected_to_block("https://yandex.ru/search/?text=x", "")

    def test_the_body_cannot_be_consulted_at_all(self):
        """Content phrases are the detector's other arm and must stay out.

        Right after `goto` the DOM is still settling, so a half-rendered page
        carrying a trigger phrase would fire — and the size ceiling that
        normally protects real content from exactly that has not been reached
        yet. The URL is the one signal that is true the instant navigation
        commits.

        Asserted on the signature rather than the source text: a function that
        takes no body cannot read one, whatever its comments happen to say.
        """
        import inspect

        params = list(inspect.signature(redirected_to_block).parameters)

        assert params == ["requested_url", "current_url"]


@pytest.mark.asyncio
async def test_camoufox_skips_the_selector_wait_once_the_url_says_blocked(monkeypatch):
    """The 45s this change exists to stop spending."""
    from tests.browser.test_camoufox_runner import _mock_page
    from src.browser.camoufox_runner import CamoufoxRunner

    page = _mock_page(monkeypatch)
    page.url = "https://yandex.ru/showcaptcha?cc=1"

    res = await CamoufoxRunner(timeout_ms=45000).fetch(
        url="https://yandex.ru/search/?text=x", device="desktop", proxy=None,
        headers=None, wait_until="load", wait_for_selector="li.serp-item",
        timeout_ms=45000, screenshot=False,
    )

    page.wait_for_selector.assert_not_awaited()
    # The verdict is unchanged — the fast path declines to WAIT, it does not
    # decide. The classifier still runs on the full page afterwards.
    assert res.ok is False
    assert res.blocked is True


@pytest.mark.asyncio
async def test_camoufox_still_waits_on_an_ordinary_page(monkeypatch):
    """The control: nothing about the normal path may change."""
    from tests.browser.test_camoufox_runner import _mock_page
    from src.browser.camoufox_runner import CamoufoxRunner

    page = _mock_page(monkeypatch)
    page.url = "https://yandex.ru/search/?text=x"

    await CamoufoxRunner(timeout_ms=45000).fetch(
        url="https://yandex.ru/search/?text=x", device="desktop", proxy=None,
        headers=None, wait_until="load", wait_for_selector="li.serp-item",
        timeout_ms=45000, screenshot=False,
    )

    page.wait_for_selector.assert_awaited_once()


class TestTheInterstitialThatIsNotABlock:
    """`/showcaptchafast` contains `/showcaptcha` and is NOT a block.

    It is Yandex's transparent browser check: it self-resolves in a few seconds
    and redirects to the real SERP. `yandex_search` ships
    `wait_for_selector: li.serp-item` precisely so the wait outlives it — and an
    earlier draft of the fast path skipped exactly that wait on a substring
    match, turning an 18-result SERP into a hard `blocked` and burning three
    exits chasing it. Reproduced against a real browser before it was narrowed.
    """

    REQUESTED = "https://yandex.ru/search/?text=x&lr=225"

    def test_the_self_resolving_interstitial_is_left_alone(self):
        assert not redirected_to_block(
            self.REQUESTED, "https://yandex.ru/showcaptchafast?cc=1&mt=ABC"
        )

    def test_the_real_smartcaptcha_still_counts(self):
        assert redirected_to_block(
            self.REQUESTED, "https://yandex.ru/showcaptcha?cc=1&mt=ABC"
        )

    def test_a_trailing_slash_does_not_hide_the_real_one(self):
        assert redirected_to_block(self.REQUESTED, "https://yandex.ru/showcaptcha/")

    def test_a_marker_in_the_query_string_is_not_a_path(self):
        """Google's own block URL embeds the page it blocked —
        /sorry/index?continue=<original> — so a whole-URL substring test would
        fire on whatever that original happened to contain."""
        assert not redirected_to_block(
            self.REQUESTED, "https://yandex.ru/search/?text=x&next=/showcaptcha"
        )
        assert redirected_to_block(
            "https://www.google.com/search?q=x",
            "https://www.google.com/sorry/index?continue=https://www.google.com/search",
        )


@pytest.mark.asyncio
async def test_playwright_runner_skips_the_wait_once_the_url_says_blocked():
    """The Chromium path had the identical waste, so it gets the identical gate."""
    from tests.browser.test_selector_timeout_classification import (
        CAPTCHA_HTML, CAPTCHA_URL, _page, _playwright_fetch,
    )

    page = _page(content=CAPTCHA_HTML, url=CAPTCHA_URL)

    res = await _playwright_fetch(page)

    page.wait_for_selector.assert_not_awaited()
    assert res.blocked is True
    assert res.ok is False


@pytest.mark.asyncio
async def test_playwright_runner_still_waits_on_an_ordinary_page():
    from tests.browser.test_selector_timeout_classification import (
        SERP_URL, _page, _playwright_fetch,
    )

    page = _page(content="<html>results</html>", url=SERP_URL, selector_times_out=False)

    await _playwright_fetch(page)

    page.wait_for_selector.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_self_resolving_interstitial_still_gets_its_wait():
    """The regression the narrowed matcher exists to prevent, end to end.

    `/showcaptchafast` resolves into a real SERP if the wait is allowed to
    outlive it; skipping the wait there was measured to turn 18 organic results
    into a hard `blocked` and burn three exits.
    """
    from tests.browser.test_selector_timeout_classification import _page, _playwright_fetch

    page = _page(
        content="<html>verification</html>",
        url="https://yandex.ru/showcaptchafast?cc=1&mt=ABC",
        selector_times_out=False,
    )

    await _playwright_fetch(page)

    page.wait_for_selector.assert_awaited_once()
