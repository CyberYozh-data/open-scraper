from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.proxy.base import ProxyConfigError
from src.queue.scrape_runner import (
    apply_default_proxy_country,
    looks_like_proxy_failure,
    run_scrape,
)
from src.settings import settings

pytestmark = pytest.mark.asyncio


def test_proxy_failure_heuristics():
    assert looks_like_proxy_failure(403, None) is True
    assert looks_like_proxy_failure(None, "tunnel connection failed") is True
    assert looks_like_proxy_failure(404, "not found") is False
    assert looks_like_proxy_failure(None, None) is False


def test_navigation_deadline_is_not_a_proxy_failure():
    """A page that loaded too slowly is not evidence the proxy is bad.

    Playwright raises this when the navigation budget expires, which means the
    proxy already carried the connection — rotating it and starting over cannot
    recover the page, it just spends the budget again. Observed live: three
    attempts per URL, all timing out identically, 90s burned on a page the
    caller had already given up waiting for.
    """
    # Both engines' real wrappers: PlaywrightRunner prefixes a literal
    # "PlaywrightError: ", while camoufox_runner uses type(exc).__name__ — and
    # Playwright's timeout class is itself named TimeoutError, which puts the
    # needle "timeout" in the prefix.
    for wrapper in ("PlaywrightError: ", "TimeoutError: ", ""):
        message = (
            f"{wrapper}Page.goto: Timeout 30000ms exceeded.\n"
            "Call log:\n"
            '  - navigating to "https://habr.com/ru/hubs/webdev/", waiting until "load"'
        )
        assert looks_like_proxy_failure(None, message) is False, wrapper


def test_navigation_deadline_verdict_ignores_the_scraped_url():
    """The call log names the target, and needles like "dns" are three letters.

    Matching them against the whole message reads the *path being crawled* as a
    transport fault, so the verdict would depend on which page happened to be
    slow. These are real paths from the crawl that motivated this fix.
    """
    for url in (
        "https://habr.com/ru/companies/dns/",
        "https://example.com/blog/proxy-guide",
        "https://shop.example.com/tls-certificates",
        "https://example.com/about/tunnel",
    ):
        message = (
            "PlaywrightError: Page.goto: Timeout 30000ms exceeded.\n"
            f'Call log:\n  - navigating to "{url}", waiting until "load"'
        )
        assert looks_like_proxy_failure(None, message) is False, url


def test_transport_faults_remain_proxy_failures():
    """Only the navigation deadline is exempt; a bad exit still rotates."""
    assert looks_like_proxy_failure(None, "proxy connect timed out") is True
    assert looks_like_proxy_failure(None, "net::ERR_TIMED_OUT") is True
    # How a refused exit actually surfaces — the deadline never fires.
    assert (
        looks_like_proxy_failure(
            None, "PlaywrightError: Page.goto: net::ERR_PROXY_CONNECTION_FAILED at https://x/"
        )
        is True
    )
    # The wrapper is stripped, not the whole head: a real fault behind the
    # TimeoutError class name still rotates.
    assert looks_like_proxy_failure(None, "TimeoutError: proxy connect timed out") is True


def test_default_country_pins_rotating_residential_without_country():
    d = settings.default_proxy_country
    assert apply_default_proxy_country("prem_res_rotating", None) == {"country_code": d}
    assert apply_default_proxy_country("res_rotating", {"city": "x"}) == {"city": "x", "country_code": d}


def test_default_country_respects_explicit_country():
    geo = {"country_code": "DE"}
    assert apply_default_proxy_country("prem_res_rotating", geo) == geo


def test_default_country_ignores_non_rotating_types():
    # direct + static proxies keep whatever they were given (no random-country problem)
    assert apply_default_proxy_country("none", None) is None
    assert apply_default_proxy_country("res_static", None) is None
    assert apply_default_proxy_country("dc_static", {"city": "x"}) == {"city": "x"}


def test_proxy_failure_matches_firefox_error_codes():
    """Camoufox is Firefox: goto failures surface NS_ERROR_* codes, which must
    rotate the proxy the same way Chromium's net::ERR_* equivalents do."""
    assert looks_like_proxy_failure(None, "Page.goto: NS_ERROR_CONNECTION_REFUSED") is True
    assert looks_like_proxy_failure(None, "Page.goto: NS_ERROR_UNKNOWN_HOST") is True
    assert looks_like_proxy_failure(None, "Page.goto: NS_ERROR_NET_RESET") is True
    # boundary: aborts carry no NS_ERROR_ prefix and are not proxy failures
    assert looks_like_proxy_failure(None, "Page.goto: NS_BINDING_ABORTED") is False


async def test_run_scrape_success_envelope(monkeypatch):
    fetch_result = MagicMock(
        ok=True, html="<html>hi</html>", final_url="https://e.com/", status_code=200,
        screenshot_b64=None, applied_user_agent="ua", applied_locale=None,
        applied_timezone=None, applied_accept_language=None, element_status=None,
        storage_state={"cookies": []}, error=None,
    )
    runner = MagicMock()
    runner.fetch = AsyncMock(return_value=fetch_result)

    session = MagicMock()
    session.max_attempts.return_value = 1
    session.current_proxy.return_value = None
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(return_value=session),
    )

    out = await run_scrape(
        runner, "req_t1",
        {"url": "https://e.com", "device": "desktop", "proxy_type": "none"},
        storage_state=None,
    )
    assert out["ok"] is True
    assert out["result"]["request_id"] == "req_t1"
    assert out["result"]["meta"]["status_code"] == 200
    assert out["storage_state"] == {"cookies": []}


async def test_run_scrape_surfaces_fetch_failure_in_meta(monkeypatch):
    """H5: a degraded fetch (ok=False) still produces a result envelope, but
    meta.fetch_ok must expose the failure instead of it being masked by the
    envelope's top-level ok=True (only warnings carried it before)."""
    # Page loaded but the fetch reports ok=False for a non-retryable reason
    # (not a proxy failure, not a block) so retries stop immediately and we can
    # assert the surfaced meta. status_code=200 + blocked=False keep both the
    # proxy-failure and block rotation paths from firing.
    fetch_result = MagicMock(
        ok=False, blocked=False, html="<html></html>", final_url="https://e.com/",
        status_code=200, screenshot_b64=None, applied_user_agent=None,
        applied_locale=None, applied_timezone=None, applied_accept_language=None,
        element_status=None, storage_state=None,
        error="content extraction failed",
    )
    runner = MagicMock()
    runner.fetch = AsyncMock(return_value=fetch_result)

    session = MagicMock()
    session.max_attempts.return_value = 1
    session.current_proxy.return_value = None
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(return_value=session),
    )

    out = await run_scrape(
        runner, "req_h5",
        {"url": "https://e.com", "device": "desktop", "proxy_type": "none"},
        storage_state=None,
    )
    assert out["ok"] is True  # envelope still carries a structured result
    assert out["result"]["meta"]["fetch_ok"] is False


async def test_run_scrape_classifies_proxy_config_error(monkeypatch, caplog):
    """An invalid proxy configuration (e.g. a region name from another country)
    is user input, not a code failure: the envelope must carry a clear error
    message and the log must stay at WARNING — no 'unexpected error' ERROR."""
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(side_effect=ProxyConfigError("no v2 geo suffix for name='Dagestan'")),
    )
    with caplog.at_level(logging.WARNING, logger="src.queue.scrape_runner"):
        out = await run_scrape(
            MagicMock(), "req_cfg",
            {"url": "https://e.com", "device": "desktop", "proxy_type": "prem_res_rotating"},
            storage_state=None,
        )
    assert out["ok"] is False
    assert out["error"].startswith("ProxyConfigError:") and "Dagestan" in out["error"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_run_scrape_rotates_proxy_on_captcha(monkeypatch):
    """A captcha/block (ok=False, blocked=True) must rotate the proxy and retry,
    not stop dead after attempt 1 — the previous behaviour, because the captcha
    error string isn't classified as a proxy failure."""
    def _fr(**kw):
        base = dict(
            status_code=200, screenshot_b64=None, applied_user_agent=None,
            applied_locale=None, applied_timezone=None, applied_accept_language=None,
            element_status=None, storage_state=None,
        )
        base.update(kw)
        return MagicMock(**base)

    blocked = _fr(ok=False, blocked=True, html="<html>captcha</html>",
                  final_url="https://www.google.com/sorry/",
                  error="Captcha/block detected by heuristic")
    success = _fr(ok=True, blocked=False, html="<html>ok</html>",
                  final_url="https://e.com/", error=None, element_status="not_requested")
    runner = MagicMock()
    runner.fetch = AsyncMock(side_effect=[blocked, success])

    session = MagicMock()
    session.max_attempts.return_value = 2
    session.current_proxy.return_value = None
    session.on_failure = AsyncMock(return_value=True)  # rotation available
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(return_value=session),
    )

    out = await run_scrape(
        runner, "req_block",
        {"url": "https://www.google.com/search", "device": "desktop", "proxy_type": "res_rotating"},
        storage_state=None,
    )
    assert runner.fetch.await_count == 2       # retried after the block
    session.on_failure.assert_awaited()        # rotated to a fresh proxy
    assert out["result"]["meta"]["fetch_ok"] is True  # second attempt succeeded


def _fr(**kw):
    base = dict(
        status_code=200, screenshot_b64=None, applied_user_agent=None,
        applied_locale=None, applied_timezone=None, applied_accept_language=None,
        element_status=None, storage_state=None,
    )
    base.update(kw)
    return MagicMock(**base)


async def test_run_scrape_rotates_proxy_on_ban_status(monkeypatch):
    """A ban HTTP status (eBay's 403) returns an HTML body but ok=False/blocked=True
    (via classify_fetch); the loop must rotate instead of short-circuiting on ok.
    This is the exact eBay regression: previously ok=True ended retries at 0."""
    banned = _fr(ok=False, blocked=True, status_code=403,
                 html="<html><body>Error Page</body></html>",
                 final_url="https://www.ebay.com/sch/i.html",
                 error="HTTP 403 (proxy ban / rate limit)")
    success = _fr(ok=True, blocked=False, html="<html>ok</html>",
                  final_url="https://www.ebay.com/sch/i.html",
                  error=None, element_status="not_requested")
    runner = MagicMock()
    runner.fetch = AsyncMock(side_effect=[banned, success])

    session = MagicMock()
    session.max_attempts.return_value = 2
    session.current_proxy.return_value = None
    session.on_failure = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(return_value=session),
    )

    out = await run_scrape(
        runner, "req_ban",
        {"url": "https://www.ebay.com/sch/i.html", "device": "desktop", "proxy_type": "res_rotating"},
        storage_state=None,
    )
    assert runner.fetch.await_count == 2          # rotated past the 403
    session.on_failure.assert_awaited()
    assert out["result"]["meta"]["fetch_ok"] is True


async def test_run_scrape_rotates_on_transient_status_not_flagged_blocked(monkeypatch):
    """A transient 5xx is ok=False but blocked=False — rotation must still fire
    via looks_like_proxy_failure(status_code), exercising the OR-branch."""
    transient = _fr(ok=False, blocked=False, status_code=503,
                    html="", final_url="https://e.com/",
                    error="HTTP 503 (transient upstream failure)")
    success = _fr(ok=True, blocked=False, html="<html>ok</html>",
                  final_url="https://e.com/", error=None, element_status="not_requested")
    runner = MagicMock()
    runner.fetch = AsyncMock(side_effect=[transient, success])

    session = MagicMock()
    session.max_attempts.return_value = 2
    session.current_proxy.return_value = None
    session.on_failure = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(return_value=session),
    )

    out = await run_scrape(
        runner, "req_transient",
        {"url": "https://e.com/", "device": "desktop", "proxy_type": "res_rotating"},
        storage_state=None,
    )
    assert runner.fetch.await_count == 2          # rotated despite blocked=False
    session.on_failure.assert_awaited()
    assert out["result"]["meta"]["fetch_ok"] is True


async def test_run_scrape_captcha_rotation_is_bounded(monkeypatch):
    """A target captcha'd on EVERY attempt must stop after max_attempts fetches
    (can't burn the whole proxy pool) and surface fetch_ok=False."""
    def _blocked():
        return MagicMock(
            ok=False, blocked=True, html="<html>captcha</html>",
            final_url="https://www.google.com/sorry/", status_code=200,
            error="Captcha/block detected by heuristic", screenshot_b64=None,
            applied_user_agent=None, applied_locale=None, applied_timezone=None,
            applied_accept_language=None, element_status=None, storage_state=None,
        )
    runner = MagicMock()
    runner.fetch = AsyncMock(side_effect=[_blocked(), _blocked(), _blocked()])

    session = MagicMock()
    session.max_attempts.return_value = 2  # the bound
    session.current_proxy.return_value = None
    session.on_failure = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(return_value=session),
    )

    out = await run_scrape(
        runner, "req_perma_block",
        {"url": "https://www.google.com/search", "device": "desktop", "proxy_type": "res_rotating"},
        storage_state=None,
    )
    assert runner.fetch.await_count == 2  # capped at max_attempts, not unbounded
    assert out["result"]["meta"]["fetch_ok"] is False
    assert out["result"]["meta"]["retries"] == 2


_DEADLINE_ERROR = (
    "PlaywrightError: Page.goto: Timeout 30000ms exceeded.\n"
    'Call log:\n  - navigating to "https://habr.com/ru/hubs/webdev/", '
    'waiting until "load"'
)


def _deadline_result():
    return MagicMock(
        ok=False, blocked=False, html="", final_url=None, status_code=None,
        error=_DEADLINE_ERROR, screenshot_b64=None, applied_user_agent=None,
        applied_locale=None, applied_timezone=None, applied_accept_language=None,
        element_status=None, storage_state=None, applied_warmup=None,
    )


def _deadline_session(monkeypatch, attempts=3):
    session = MagicMock()
    session.max_attempts.return_value = attempts
    session.current_proxy.return_value = None
    session.on_failure = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(return_value=session),
    )
    return session


async def test_run_scrape_grants_exactly_one_rotation_on_a_navigation_deadline(monkeypatch):
    """Two attempts, not three: one retry keeps an expired exit recoverable.

    A hung exit, or one answering 407 because the subscription lapsed, is
    indistinguishable from a slow page — so a single rotation still gets
    `recover()` a chance to mint fresh credentials. What it must not do is burn
    the full budget three times over, which is what put the caller's 90s poll
    out of reach.

    Asserted at this level rather than on the helper alone, because the helper
    could stop being consulted and the helper-level tests would still pass.
    """
    runner = MagicMock()
    runner.fetch = AsyncMock(return_value=_deadline_result())
    session = _deadline_session(monkeypatch)

    out = await run_scrape(
        runner, "req_slow_page",
        {"url": "https://habr.com/ru/hubs/webdev/", "device": "desktop",
         "proxy_type": "prem_res_rotating"},
        storage_state=None,
    )

    assert runner.fetch.await_count == 2
    assert session.on_failure.await_count == 1
    assert out["result"]["meta"]["retries"] == 1
    assert out["result"]["meta"]["fetch_ok"] is False


async def test_run_scrape_logs_the_exit_when_it_gives_up_on_a_deadline(monkeypatch, caplog):
    """The log is the whole mitigation for the case we decline to retry further.

    Without the exit in it, "suspect the exit" is not actionable — and a lapsed
    subscription would read as a slow website.
    """
    runner = MagicMock()
    runner.fetch = AsyncMock(return_value=_deadline_result())
    _deadline_session(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="src.queue.scrape_runner"):
        await run_scrape(
            runner, "req_slow_page",
            {"url": "https://habr.com/ru/hubs/webdev/", "device": "desktop",
             "proxy_type": "prem_res_rotating"},
            storage_state=None,
        )

    giving_up = [r for r in caplog.records if "navigation deadline" in r.getMessage()]
    assert len(giving_up) == 1, [r.getMessage() for r in caplog.records]
    message = giving_up[0].getMessage()
    assert "exit=" in message
    assert "https://habr.com/ru/hubs/webdev/" in message
    assert "timeout_ms=30000" in message


async def test_run_scrape_still_rotates_fully_on_a_selector_deadline(monkeypatch):
    """The exemption is anchored to page.goto on purpose.

    wait_for_selector timing out is a different signal — on a SERP preset a
    missing selector usually means a block — so it keeps the full retry budget.

    The error string is the one the runners actually emit now. It used to be a
    raw `PlaywrightError: ... Timeout ...`, which reached this decision only
    because "timeout" happened to be in the proxy-failure needle list; rotating
    on a missing selector is deliberate, so it is matched deliberately.
    """
    runner = MagicMock()
    result = _deadline_result()
    result.error = "selector_not_found: li.serp-item"
    runner.fetch = AsyncMock(return_value=result)
    session = _deadline_session(monkeypatch)

    await run_scrape(
        runner, "req_missing_selector",
        {"url": "https://www.google.com/search", "device": "desktop",
         "proxy_type": "prem_res_rotating"},
        storage_state=None,
    )

    assert runner.fetch.await_count == 3
    assert session.on_failure.await_count == 3


async def test_run_scrape_error_envelope(monkeypatch):
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(side_effect=RuntimeError("no_more_proxies")),
    )
    out = await run_scrape(MagicMock(), "req_t2",
                           {"url": "https://e.com", "proxy_type": "res_static"}, None)
    assert out["ok"] is False and "no_more_proxies" in out["error"]


async def test_run_scrape_echoes_prem_targeting_and_warmup(monkeypatch):
    """The result meta echoes the resolved prem targeting (read from the proxy
    config's targeting_suffix — never the credential) and what the warmup
    actually did (read from the fetch result), so clients see what was applied."""
    applied_warmup = {"type": "homepage", "url": "https://e.com/", "dwell_ms": 1500}
    fetch_result = MagicMock(
        ok=True, html="<html>hi</html>", final_url="https://e.com/", status_code=200,
        screenshot_b64=None, applied_user_agent="ua", applied_locale=None,
        applied_timezone=None, applied_accept_language=None, element_status=None,
        storage_state=None, error=None, applied_warmup=applied_warmup,
    )
    runner = MagicMock()
    runner.fetch = AsyncMock(return_value=fetch_result)

    session = MagicMock()
    session.max_attempts.return_value = 1
    session.current_proxy.return_value = MagicMock(targeting_suffix="c-us-filter-iqs")
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(return_value=session),
    )

    out = await run_scrape(
        runner, "req_prem",
        {"url": "https://e.com", "device": "desktop",
         "proxy_type": "prem_res_rotating",
         "warmup": {"type": "homepage", "dwell_ms": 1500}},
        storage_state=None,
    )
    meta = out["result"]["meta"]
    assert meta["applied_prem_targeting"] == "c-us-filter-iqs"
    assert meta["applied_warmup"] == applied_warmup
