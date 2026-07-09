from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.browser.runner import warmup_origin, run_warmup


def test_warmup_origin_from_search_url():
    assert warmup_origin("https://yandex.ru/search/?text=x&lr=213") == "https://yandex.ru/"
    assert warmup_origin("http://example.com/a/b?q=1") == "http://example.com/"
    assert warmup_origin("not-a-url") is None


@pytest.mark.asyncio
async def test_run_warmup_visits_origin_then_dwells():
    page = AsyncMock()
    applied = await run_warmup(
        page, "https://yandex.ru/search/?text=x",
        {"type": "homepage"}, timeout_ms=40000, default_dwell_ms=2500,
    )
    page.goto.assert_awaited_once()
    assert page.goto.call_args.args[0] == "https://yandex.ru/"
    page.wait_for_timeout.assert_awaited_once_with(2500)
    # Reports what actually ran: the resolved origin, not the requested config.
    assert applied == {"type": "homepage", "url": "https://yandex.ru/", "dwell_ms": 2500}


@pytest.mark.asyncio
async def test_run_warmup_noop_when_disabled_or_unknown_type():
    page = AsyncMock()
    assert await run_warmup(page, "https://yandex.ru/s", None, timeout_ms=1, default_dwell_ms=1) is None
    assert await run_warmup(page, "https://yandex.ru/s", {"type": "other"}, timeout_ms=1, default_dwell_ms=1) is None
    page.goto.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_warmup_swallows_errors():
    page = AsyncMock()
    page.goto.side_effect = RuntimeError("boom")
    # Must not raise, and reports None so callers don't claim a failed warmup ran.
    assert await run_warmup(page, "https://yandex.ru/s", {"type": "homepage"},
                            timeout_ms=1, default_dwell_ms=1) is None


@pytest.mark.asyncio
async def test_camoufox_fetch_runs_warmup(monkeypatch):
    import src.browser.camoufox_runner as cr

    page = AsyncMock()
    page.goto.return_value = AsyncMock(status=200)
    page.content.return_value = "<html>OrganicTitle</html>"
    page.url = "https://yandex.ru/search/?text=x"
    page.evaluate.return_value = {}

    browser = AsyncMock()
    browser.new_page.return_value = page

    class _CM:
        async def __aenter__(self): return browser
        async def __aexit__(self, *a): return None

    monkeypatch.setattr(cr, "AsyncCamoufox", lambda **k: _CM())

    runner = cr.CamoufoxRunner(timeout_ms=40000)
    await runner.fetch(
        url="https://yandex.ru/search/?text=x", device="desktop", proxy=None,
        headers=None, wait_until="domcontentloaded", wait_for_selector=None,
        timeout_ms=None, screenshot=False, warmup={"type": "homepage"},
    )
    # first goto is the warmup origin, second is the real url
    assert page.goto.await_args_list[0].args[0] == "https://yandex.ru/"
    assert page.goto.await_args_list[-1].args[0] == "https://yandex.ru/search/?text=x"


@pytest.mark.asyncio
async def test_scrape_runner_threads_warmup_to_fetch():
    from src.browser.runner import FetchResult
    from src.queue import scrape_runner

    runner = AsyncMock()
    runner.fetch.return_value = FetchResult(
        html="<html>ok</html>", final_url="u", status_code=200,
        screenshot_b64=None, ok=True, error=None,
    )
    req = {"url": "https://yandex.ru/search/?text=x", "warmup": {"type": "homepage"}}
    await scrape_runner.run_scrape(runner, "rid", req, None)
    assert runner.fetch.await_args.kwargs["warmup"] == {"type": "homepage"}


@pytest.mark.asyncio
async def test_run_warmup_custom_visits_given_url():
    page = AsyncMock()
    applied = await run_warmup(
        page, "https://yandex.ru/search/?text=x",
        {"type": "custom", "url": "https://warm.example/seed"},
        timeout_ms=40000, default_dwell_ms=2500,
    )
    page.goto.assert_awaited_once()
    assert page.goto.call_args.args[0] == "https://warm.example/seed"
    page.wait_for_timeout.assert_awaited_once_with(2500)
    assert applied == {"type": "custom", "url": "https://warm.example/seed", "dwell_ms": 2500}
