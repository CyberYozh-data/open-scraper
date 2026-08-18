"""Amazon's throttle page is a block, and it arrives with HTTP 200.

`amazon_search/us` came back `fetch_ok=True` with zero rows. The page was 2.3 KB
whose entire visible text is "Sorry! Something went wrong!" — Amazon's anti-bot
/ throttle response. Measured over four attempts it arrived with HTTP 503 every
time on one sweep and HTTP 200 on another, from the same URL: the 503 variant
was correctly classified as transient and rotated the exit, the 200 variant was
accepted as a successful fetch. So the retry loop stopped rotating on the exact
response that most needed a fresh exit, and only the preset's `required: true`
warning revealed anything was wrong.

For contrast, a healthy Amazon page is 2-3.7 MB and titled "Amazon.co.uk : laptop".
"""
from __future__ import annotations

import pytest

from src.browser.runner import classify_fetch, looks_like_captcha_or_block

# Trimmed to what the live page actually contains.
SORRY_PAGE = (
    "<!doctype html><html><head><title>Sorry! Something went wrong!</title></head>"
    "<body><a href='/ref=cs_503_link'><img src='/dogs.jpg' alt='Dogs of Amazon'></a>"
    "</body></html>"
)


class TestAmazonSorryPage:
    def test_it_is_recognised_as_a_block(self):
        assert looks_like_captcha_or_block(SORRY_PAGE, final_url="https://www.amazon.com/s?k=laptop")

    def test_a_200_response_carrying_it_does_not_count_as_success(self):
        """The whole defect, end to end from the fixture.

        An earlier version of this test called classify_fetch(200,
        captcha_detected=True) directly — which is pre-existing plumbing and
        passes whether or not the page is recognised at all. Driving the verdict
        from the page makes it depend on the signal under test.
        """
        detected = looks_like_captcha_or_block(
            SORRY_PAGE, final_url="https://www.amazon.com/s?k=laptop"
        )
        ok, blocked, error = classify_fetch(200, captcha_detected=detected)

        assert ok is False
        assert blocked is True
        assert error

    def test_the_503_variant_is_now_named_a_block_rather_than_a_transient(self):
        """A behaviour change worth pinning, not hiding.

        Amazon serves this page with either status. The 503 variant already
        rotated — as a transient upstream failure — so the outcome is unchanged;
        what changes is the reason the caller is given. "Captcha/block detected"
        is the true one, and it is what tells an operator the exit is burned
        rather than the upstream being briefly unwell.
        """
        detected = looks_like_captcha_or_block(
            SORRY_PAGE, final_url="https://www.amazon.com/s?k=laptop"
        )
        ok, blocked, error = classify_fetch(503, captcha_detected=detected)

        assert ok is False
        assert blocked is True, "a burned exit, not a flaky upstream"
        assert "transient" not in (error or "").lower()

    def test_a_real_amazon_page_is_not_flagged(self):
        """Guards the SIZE CEILING, which is what makes a body-phrase match safe
        at all: a 2 MB SERP is never inspected for phrases. This one does not
        move when the signal is added or removed — that is the point. It fails
        if someone raises `_MAX_BLOCK_PAGE_BYTES` past a real page's size.
        """
        real = (
            "<html><head><title>Amazon.co.uk : laptop</title></head><body>"
            + "<div data-component-type='s-search-result'>a laptop</div>" * 400
            + "sorry! something went wrong is a phrase in a product review"
            + "</body></html>"
        )
        assert len(real) > 20_000, "the fixture must exceed the block-page ceiling"

        assert not looks_like_captcha_or_block(real, final_url="https://www.amazon.co.uk/s?k=laptop")

    def test_a_small_page_merely_apologising_is_not_enough(self):
        """`sorry` and `something went wrong` are ordinary words apart.

        Matching either alone would flag a genuine small error page a caller
        deliberately scraped, and turn it into three burned exits.
        """
        assert not looks_like_captcha_or_block(
            "<html><body>Sorry, we are closed today.</body></html>",
            final_url="https://shop.example/",
        )
        assert not looks_like_captcha_or_block(
            "<html><body>Something went wrong loading the gallery.</body></html>",
            final_url="https://shop.example/",
        )
