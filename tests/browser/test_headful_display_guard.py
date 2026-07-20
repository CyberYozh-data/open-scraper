import types

import pytest

from src.browser.runner import PlaywrightRunner


@pytest.mark.asyncio
async def test_headful_without_display_fails_fast_with_a_clear_message(monkeypatch):
    """Better than Playwright's opaque launch crash on a misconfigured host."""
    monkeypatch.delenv("DISPLAY", raising=False)
    runner = PlaywrightRunner(headless=False, block_assets=True, timeout_ms=1000)
    with pytest.raises(RuntimeError, match="requires an X display"):
        await runner.start()


@pytest.mark.asyncio
async def test_headful_with_a_display_passes_the_guard(monkeypatch):
    """Guard is DISPLAY-only: with one set it must not fire. Stop before the
    real launch so this stays a unit test (no browser process)."""
    monkeypatch.setenv("DISPLAY", ":99")
    runner = PlaywrightRunner(headless=False, block_assets=True, timeout_ms=1000)
    boom = RuntimeError("reached playwright start")

    async def _fake_start():
        raise boom

    monkeypatch.setattr(
        "src.browser.runner.async_playwright", lambda: types.SimpleNamespace(start=_fake_start)
    )
    with pytest.raises(RuntimeError) as exc:
        await runner.start()
    assert exc.value is boom  # got past the guard, not blocked by it


@pytest.mark.asyncio
async def test_headless_without_a_display_does_not_trip_the_guard(monkeypatch):
    """The guard is for headful only. Dropping `not self.headless` from its
    condition would break every headless launch on a DISPLAY-less host — which
    is the normal case — so pin the negative side explicitly."""
    monkeypatch.delenv("DISPLAY", raising=False)
    runner = PlaywrightRunner(headless=True, block_assets=True, timeout_ms=1000)
    boom = RuntimeError("reached playwright start")

    async def _fake_start():
        raise boom

    monkeypatch.setattr(
        "src.browser.runner.async_playwright", lambda: types.SimpleNamespace(start=_fake_start)
    )
    with pytest.raises(RuntimeError) as exc:
        await runner.start()
    assert exc.value is boom  # the guard stayed out of the way
