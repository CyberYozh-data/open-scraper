from __future__ import annotations

import pytest
from src.browser.camoufox_runner import CamoufoxRunner, build_camoufox_options


def test_build_options_maps_normalized_fields():
    opts = build_camoufox_options(
        proxy={"server": "http://h:1", "username": "u", "password": "p"},
        block_assets=True, webrtc_block=True,
        humanize=True, spoof_os="windows", block_webgl=True, addons=["ublock"],
    )
    assert opts["geoip"] is True
    assert opts["block_images"] is True
    assert opts["block_webrtc"] is True
    assert opts["humanize"] is True
    assert opts["os"] == "windows"
    assert opts["block_webgl"] is True
    assert opts["addons"] == ["ublock"]
    assert opts["proxy"]["server"] == "http://h:1"


def test_build_options_defaults_are_sane():
    opts = build_camoufox_options(proxy=None, block_assets=False, webrtc_block=False)
    assert opts["geoip"] is True          # always geoip when proxying
    assert opts["block_images"] is False
    assert opts["humanize"] is False
    assert opts["block_webgl"] is False
    assert opts.get("os") is None
    assert opts.get("addons") is None


def test_runner_is_never_warm():
    r = CamoufoxRunner(timeout_ms=30000)
    assert r.is_started() is False


class _ACM:
    """Minimal async-context-manager wrapper around a mock browser."""
    def __init__(self, browser):
        self._browser = browser
    async def __aenter__(self):
        return self._browser
    async def __aexit__(self, *a):
        return False


def _mock_page(monkeypatch):
    from unittest.mock import AsyncMock, Mock
    page = AsyncMock()
    page.goto = AsyncMock(return_value=Mock(status=200))
    page.content = AsyncMock(return_value="<html><body>real results</body></html>")
    page.url = "https://ya.ru/"
    page.evaluate = AsyncMock(return_value={
        "ua": "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0",
        "locale": "ru", "tz": "Asia/Almaty", "al": "ru,en;q=0.9",
    })
    page.screenshot = AsyncMock(return_value=b"PNGBYTES")
    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)
    monkeypatch.setattr("src.browser.camoufox_runner.AsyncCamoufox", lambda **kw: _ACM(browser))
    return page


@pytest.mark.asyncio
async def test_fetch_reports_applied_fingerprint(monkeypatch):
    """Camoufox aligns locale/timezone to the exit IP via geoip; the runner must
    read them back so meta.applied_* isn't null (it was, vs the Playwright path)."""
    _mock_page(monkeypatch)
    res = await CamoufoxRunner(timeout_ms=30000).fetch(
        url="https://ya.ru/", device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
        screenshot=False,
    )
    assert res.applied_user_agent and "Firefox" in res.applied_user_agent
    assert res.applied_locale == "ru"
    assert res.applied_timezone == "Asia/Almaty"
    assert res.applied_accept_language == "ru,en;q=0.9"


@pytest.mark.asyncio
async def test_fetch_captures_screenshot_when_requested(monkeypatch):
    """screenshot=True must yield a base64 PNG on Camoufox (was always None)."""
    _mock_page(monkeypatch)
    res = await CamoufoxRunner(timeout_ms=30000).fetch(
        url="https://ya.ru/", device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
        screenshot=True, block_assets=True,
    )
    assert res.screenshot_b64 is not None
    import base64
    assert base64.b64decode(res.screenshot_b64) == b"PNGBYTES"


@pytest.mark.asyncio
async def test_fetch_no_screenshot_when_not_requested(monkeypatch):
    _mock_page(monkeypatch)
    res = await CamoufoxRunner(timeout_ms=30000).fetch(
        url="https://ya.ru/", device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
        screenshot=False,
    )
    assert res.screenshot_b64 is None


@pytest.mark.asyncio
async def test_fetch_screenshot_full_page_status(monkeypatch):
    """Full-page screenshot (no selector) reports the same status as Playwright."""
    _mock_page(monkeypatch)
    res = await CamoufoxRunner(timeout_ms=30000).fetch(
        url="https://ya.ru/", device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
        screenshot=True, block_assets=True,
    )
    assert res.element_status == "not_requested"


@pytest.mark.asyncio
async def test_fetch_meta_readback_failure_does_not_break_fetch(monkeypatch):
    """If the applied-meta page.evaluate raises, the fetch still succeeds with
    applied_* left None (best-effort)."""
    page = _mock_page(monkeypatch)
    from unittest.mock import AsyncMock
    page.evaluate = AsyncMock(side_effect=RuntimeError("evaluate blew up"))
    res = await CamoufoxRunner(timeout_ms=30000).fetch(
        url="https://ya.ru/", device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
        screenshot=False,
    )
    assert res.ok is True
    assert res.applied_user_agent is None
    assert res.applied_locale is None
    assert res.applied_timezone is None


@pytest.mark.asyncio
async def test_fetch_applies_extra_headers(monkeypatch):
    """User-supplied headers must reach the page (they were silently dropped)."""
    page = _mock_page(monkeypatch)
    await CamoufoxRunner(timeout_ms=30000).fetch(
        url="https://ya.ru/", device="desktop", proxy=None,
        headers={"X-Custom": "1", "Accept-Language": "de-DE"},
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
        screenshot=False,
    )
    page.set_extra_http_headers.assert_awaited_once_with(
        {"X-Custom": "1", "Accept-Language": "de-DE"}
    )


@pytest.mark.asyncio
async def test_fetch_accept_language_override_is_echoed(monkeypatch):
    """An explicit Accept-Language wins on the wire, so applied_* must report
    it — navigator.languages keeps the fingerprint value (here ru,en;q=0.9)."""
    _mock_page(monkeypatch)
    res = await CamoufoxRunner(timeout_ms=30000).fetch(
        url="https://ya.ru/", device="desktop", proxy=None,
        headers={"Accept-Language": "de-DE"},
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
        screenshot=False,
    )
    assert res.applied_accept_language == "de-DE"


@pytest.mark.asyncio
async def test_fetch_drops_user_agent_header(monkeypatch, caplog):
    """Camoufox owns the UA (rotating fingerprint); a network-level override
    would desync it from navigator.userAgent, so it must be filtered out."""
    import logging
    page = _mock_page(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="src.browser.camoufox_runner"):
        await CamoufoxRunner(timeout_ms=30000).fetch(
            url="https://ya.ru/", device="desktop", proxy=None,
            headers={"User-Agent": "curl/8", "X-Keep": "yes"},
            wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
            screenshot=False,
        )
    page.set_extra_http_headers.assert_awaited_once_with({"X-Keep": "yes"})
    assert any("User-Agent" in r.message for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [None, {}, {"User-Agent": "curl/8"}])
async def test_fetch_no_applicable_headers_skips_header_call(monkeypatch, headers):
    page = _mock_page(monkeypatch)
    await CamoufoxRunner(timeout_ms=30000).fetch(
        url="https://ya.ru/", device="desktop", proxy=None, headers=headers,
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
        screenshot=False,
    )
    page.set_extra_http_headers.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_screenshot_failure_does_not_break_fetch(monkeypatch):
    """A screenshot failure must degrade to None, not fail the fetch."""
    page = _mock_page(monkeypatch)
    from unittest.mock import AsyncMock
    page.screenshot = AsyncMock(side_effect=RuntimeError("snap failed"))
    res = await CamoufoxRunner(timeout_ms=30000).fetch(
        url="https://ya.ru/", device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=None,
        screenshot=True, block_assets=True,
    )
    assert res.ok is True
    assert res.screenshot_b64 is None
    assert res.element_status == "no_screenshot"
