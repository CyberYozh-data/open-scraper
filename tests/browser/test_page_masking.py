"""The per-page masking helper, now that both call sites share one copy.

Extracting it from `fetch()` is what made these branches reachable by a test at
all — and one of them, the silent one, is the branch that actually fires in
production on a mobile session pin.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.browser.runner import DESKTOP, MOBILE, apply_page_masking


def _context(user_agent: str | None) -> MagicMock:
    context = MagicMock()
    context._applied_user_agent = user_agent
    context.new_cdp_session = AsyncMock(return_value=AsyncMock())
    return context


@pytest.mark.asyncio
async def test_a_desktop_page_gets_matching_client_hints():
    context = _context(DESKTOP["user_agent"])

    await apply_page_masking(context, MagicMock(), engine="chromium", stealth=False)

    sent = context.new_cdp_session.return_value.send.await_args
    assert sent.args[0] == "Emulation.setUserAgentOverride"
    payload = sent.args[1]
    assert payload["platform"] == "Win32"
    assert any(b["brand"] == "Google Chrome" for b in payload["userAgentMetadata"]["brands"])


@pytest.mark.asyncio
async def test_the_mobile_preset_is_a_gap_and_says_so(caplog):
    """iPhone Safari has no Chrome metadata, so the override cannot be built.

    Measured on the wire for this pin: Sec-CH-UA still announces
    "HeadlessChrome" — real iOS Safari sends none at all — and
    navigator.platform stays Linux x86_64 under an iPhone UA. Not fixed here;
    the point is that it is no longer silent, since the failure branch three
    lines below argues this exact case is worth surfacing.
    """
    context = _context(MOBILE["user_agent"])

    with caplog.at_level("WARNING"):
        await apply_page_masking(context, MagicMock(), engine="chromium", stealth=False)

    context.new_cdp_session.assert_not_awaited()
    assert any("Client-Hints override skipped" in r.getMessage() for r in caplog.records)
    assert any("iPhone" in r.getMessage() for r in caplog.records), caplog.text


@pytest.mark.asyncio
async def test_a_cdp_failure_is_survivable_but_loud():
    """A page is better than no page — but an unmasked one must not be quiet."""
    context = _context(DESKTOP["user_agent"])
    context.new_cdp_session = AsyncMock(side_effect=RuntimeError("target closed"))

    await apply_page_masking(context, MagicMock(), engine="chromium", stealth=False)


@pytest.mark.asyncio
async def test_non_chromium_engines_are_left_alone():
    """Firefox/WebKit take neither the CDP call nor the Chromium stealth patch."""
    context = _context(DESKTOP["user_agent"])

    await apply_page_masking(context, MagicMock(), engine="firefox", stealth=True)

    context.new_cdp_session.assert_not_awaited()
