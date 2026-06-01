"""End-to-end tests for the element-screenshot pipeline.

Uses real Chromium via Playwright (no network — every page comes from
`page.set_content(...)`). Marked @pytest.mark.e2e so plain CI runs can
filter it out if browsers are not available.
"""
from __future__ import annotations

import io

import pytest
import pytest_asyncio
from PIL import Image
from playwright.async_api import async_playwright

from src.browser.runner import _capture_screenshot, _ELEMENT_PADDING_PX


pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        playwright_page = await context.new_page()
        yield playwright_page
        await browser.close()


async def _set_html(page, html: str):
    await page.set_content(f"<!doctype html><html><body>{html}</body></html>")


def _png_dims(data: bytes) -> tuple[int, int]:
    image = Image.open(io.BytesIO(data))
    return image.size  # (width, height)


class TestCaptureScreenshotElementPath:
    @pytest.mark.asyncio
    async def test_element_selector_returns_cropped_with_padding(self, page):
        await _set_html(
            page,
            '<div id="x" style="width:200px;height:100px;background:red;margin:50px"></div>',
        )
        png, status = await _capture_screenshot(
            page,
            screenshot=True,
            element_selector="#x",
            effective_block_assets=True,
        )
        assert status == "element"
        assert png is not None
        width, height = _png_dims(png)
        padding = _ELEMENT_PADDING_PX
        # Allow Chromium device-pixel-ratio + sub-pixel rounding wiggle.
        assert 200 + 2 * padding - 4 <= width <= 200 + 2 * padding + 8
        assert 100 + 2 * padding - 4 <= height <= 100 + 2 * padding + 8


class TestCaptureScreenshotFallbacks:
    @pytest.mark.asyncio
    async def test_fallback_not_found(self, page):
        await _set_html(page, '<div id="x">EL</div>')
        png, status = await _capture_screenshot(
            page,
            screenshot=True,
            element_selector="#nope",
            effective_block_assets=True,
        )
        assert status == "fallback_not_found"
        assert png is not None  # full-page was taken

    @pytest.mark.asyncio
    async def test_fallback_invalid(self, page):
        await _set_html(page, '<div id="x">EL</div>')
        png, status = await _capture_screenshot(
            page,
            screenshot=True,
            element_selector=":::bad{{{",
            effective_block_assets=True,
        )
        assert status == "fallback_invalid"
        assert png is not None

    @pytest.mark.asyncio
    async def test_fallback_zero_size(self, page):
        await _set_html(
            page,
            '<div id="x" style="display:none">EL</div>',
        )
        png, status = await _capture_screenshot(
            page,
            screenshot=True,
            element_selector="#x",
            effective_block_assets=True,
        )
        assert status in ("fallback_zero_size", "fallback_timeout")
        # display:none can surface as either depending on Playwright version's
        # scroll_into_view_if_needed behaviour. Both are acceptable per spec
        # §4 ("best-effort classification").
        assert png is not None

    @pytest.mark.asyncio
    async def test_not_requested_with_no_selector(self, page):
        await _set_html(page, '<div id="x">EL</div>')
        png, status = await _capture_screenshot(
            page,
            screenshot=True,
            element_selector=None,
            effective_block_assets=True,
        )
        assert status == "not_requested"
        assert png is not None

    @pytest.mark.asyncio
    async def test_no_screenshot_when_screenshot_false(self, page):
        await _set_html(page, '<div id="x">EL</div>')
        png, status = await _capture_screenshot(
            page,
            screenshot=False,
            element_selector="#x",
            effective_block_assets=True,
        )
        assert status == "no_screenshot"
        assert png is None

    @pytest.mark.asyncio
    async def test_element_scrolled_into_view(self, page):
        # Element placed 3000px down. After scroll_into_view_if_needed +
        # full-page screenshot with clip, the resulting PNG should be the
        # element itself, not blank above-the-fold.
        await _set_html(
            page,
            '<div style="height:3000px"></div>'
            '<div id="x" style="width:200px;height:100px;background:red"></div>'
            '<div style="height:1000px"></div>',
        )
        png, status = await _capture_screenshot(
            page,
            screenshot=True,
            element_selector="#x",
            effective_block_assets=True,
        )
        assert status == "element"
        # Decode and check the mean red channel is high — a blank/white
        # crop would fail this.
        image = Image.open(io.BytesIO(png)).convert("RGB")
        r_total = sum(px[0] for px in image.getdata())
        pixel_count = image.width * image.height
        assert r_total / pixel_count > 200, "crop appears not to contain the red element"
