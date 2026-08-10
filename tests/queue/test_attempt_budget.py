"""The retry loop must stay inside PAGE_TASK_TIMEOUT_S.

`scrape_page` wraps `run_scrape` in `asyncio.wait_for(page_task_timeout_s)`, and
that ceiling covers every attempt. The loop, however, sizes each attempt from
`timeout_ms` alone, so it will start an attempt the ceiling cannot fit — and a
cancelled coroutine returns nothing at all: the block verdict, the html, and the
retry count all die with it, and the caller is handed a bare
"page task exceeded 120.0s". That is the exact opposite of what the
selector-timeout classification was added to produce.

Each test wraps `run_scrape` in the same `wait_for` production uses, so a
regression fails here the way it fails in the worker.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.browser.runner import FetchResult
from src.queue.scrape_runner import (
    affordable_attempt_timeout_ms,
    run_scrape,
    summarise_error,
)
from src.queue.envelope import ScrapeEnvelope, ScrapeOk
from src.settings import settings


def test_budget_is_divided_by_the_phases_the_runner_will_spend_it_on():
    """`timeout_ms` is applied per phase, not per attempt.

    The runners hand the same value to the warmup navigation, `page.goto` and
    `wait_for_selector` in turn, so a request asking for all three costs three
    times what a plain fetch does. Sizing on the raw number is what lets a single
    Yandex attempt (45 s x 3 + dwell = 137 s) overrun a 120 s ceiling on its own.
    """
    # goto only: the whole remaining budget is spendable on it
    assert affordable_attempt_timeout_ms(10.0, 45_000) == 10_000
    # goto + selector wait
    assert affordable_attempt_timeout_ms(10.0, 45_000, wait_for_selector="li.x") == 5_000
    # warmup navigation + its dwell + goto + selector wait
    assert affordable_attempt_timeout_ms(
        10.0, 45_000, wait_for_selector="li.x", warmup={"type": "homepage"},
    ) == int((10_000 - settings.warmup_dwell_ms) / 3)


def test_an_explicit_dwell_is_charged_instead_of_the_default():
    """A preset may set its own dwell; the arithmetic must use the one that will
    actually be spent, not the server default."""
    assert affordable_attempt_timeout_ms(
        10.0, 45_000, warmup={"type": "homepage", "dwell_ms": 4_000},
    ) == 3_000


def test_the_configured_timeout_is_a_ceiling_not_a_target():
    """Budget to spare must not lengthen an attempt past what was asked for."""
    assert affordable_attempt_timeout_ms(600.0, 30_000) == 30_000


def test_no_budget_means_no_attempt():
    """Below a tenth of a second nothing can navigate, so the exit is not worth
    spending — the caller reports the verdict it already has."""
    assert affordable_attempt_timeout_ms(0.05, 30_000) is None
    assert affordable_attempt_timeout_ms(-3.0, 30_000) is None
    # The dwell alone can eat the remainder even when the clock says there is some
    assert affordable_attempt_timeout_ms(
        2.4, 30_000, warmup={"type": "homepage", "dwell_ms": 2_500},
    ) is None


class _BudgetRunner:
    """A runner that consumes exactly the navigation budget it is handed.

    The shape of a page behind a burned exit: it never finishes, so the attempt
    costs whatever timeout the caller allowed it. Records that timeout so a test
    can assert the loop shrank the last attempt to fit.
    """

    def __init__(self, default_timeout_ms: int, *, blocked: bool = True) -> None:
        self.timeout_ms = default_timeout_ms
        self.blocked = blocked
        self.budgets: list[int] = []

    async def fetch(self, *, url: str, timeout_ms: int | None = None, **_kw) -> FetchResult:
        effective = timeout_ms or self.timeout_ms
        self.budgets.append(effective)
        await asyncio.sleep(effective / 1000)
        return FetchResult(
            html="<html>showcaptcha</html>",
            final_url=f"{url}/showcaptcha",
            status_code=200,
            screenshot_b64=None,
            ok=False,
            error="Captcha/block detected by heuristic",
            blocked=self.blocked,
        )


def _session(attempts: int) -> MagicMock:
    session = MagicMock()
    session.max_attempts.return_value = attempts
    session.current_proxy.return_value = None
    session.on_failure = AsyncMock(return_value=True)
    return session


def _patch_session(monkeypatch, session: MagicMock) -> None:
    monkeypatch.setattr(
        "src.queue.scrape_runner.proxy_resolver.open_session",
        AsyncMock(return_value=session),
    )


async def _run_under_ceiling(runner, request) -> ScrapeEnvelope:
    """Exactly what scrape_page does, so cancellation surfaces as it does live."""
    return await asyncio.wait_for(
        run_scrape(runner, "req_budget", request, None),
        timeout=settings.page_task_timeout_s,
    )


@pytest.mark.asyncio
async def test_block_survives_a_ceiling_that_cannot_fit_every_attempt(monkeypatch):
    """Three attempts at 400 ms do not fit a 1 s ceiling — the verdict must still
    reach the caller. Before the budget guard the third attempt was cancelled
    mid-flight and every attempt's findings went with it."""
    monkeypatch.setattr(settings, "page_task_timeout_s", 1.0)
    runner = _BudgetRunner(400)
    _patch_session(monkeypatch, _session(3))

    out = await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 400},
    )

    assert isinstance(out, ScrapeOk)
    meta = out.result.meta
    assert meta.fetch_ok is False
    # The rotation still happened; only the attempt that could not fit was cut.
    assert meta.retries >= 1
    assert len(runner.budgets) >= 2
    assert "Captcha/block detected by heuristic" in out.result.warnings


@pytest.mark.asyncio
async def test_the_final_attempt_is_shrunk_to_the_remaining_budget(monkeypatch):
    """An attempt that would overrun the ceiling is given the budget that is
    actually left, not the configured one. Rotating and then being cancelled
    spends a proxy for nothing, so the attempt is made to fit instead."""
    monkeypatch.setattr(settings, "page_task_timeout_s", 1.5)
    runner = _BudgetRunner(900)
    _patch_session(monkeypatch, _session(2))

    await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 900},
    )

    assert runner.budgets[0] == 900, "the first attempt keeps its configured budget"
    assert runner.budgets[-1] < 900, f"last attempt was not shrunk: {runner.budgets}"


class _ScriptedRunner:
    """Returns a prepared FetchResult per attempt, recording nothing else."""

    def __init__(self, results: list[FetchResult]) -> None:
        self._results = list(results)
        self.calls = 0

    async def fetch(self, **_kw) -> FetchResult:
        self.calls += 1
        return self._results[min(self.calls, len(self._results)) - 1]


def _blocked_page() -> FetchResult:
    return FetchResult(
        html="<html>showcaptcha</html>", final_url="https://ya.ru/showcaptcha",
        status_code=200, screenshot_b64=None, ok=False,
        error="Captcha/block detected by heuristic", blocked=True,
    )


def _transport_failure() -> FetchResult:
    return FetchResult(
        html="", final_url=None, status_code=None, screenshot_b64=None, ok=False,
        error="PlaywrightError: Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED",
    )


def _selector_miss_on_a_real_page() -> FetchResult:
    """Not a block: the SERP rendered, the preset's row selector has drifted."""
    return FetchResult(
        html="<html><div class='result'>real content</div></html>",
        final_url="https://ya.ru/search?text=x", status_code=200,
        screenshot_b64=None, ok=False,
        error="selector_not_found: li.serp-item", blocked=False,
    )


def _gateway_error_page() -> FetchResult:
    """A 5xx from the exit or an upstream CDN: html, but not the target's."""
    return FetchResult(
        html="<html><h1>Service Unavailable</h1></html>",
        final_url="https://ya.ru/search", status_code=503, screenshot_b64=None,
        ok=False, error="HTTP 503 (transient upstream failure)", blocked=False,
    )


@pytest.mark.asyncio
async def test_a_gateway_error_does_not_outrank_a_captcha(monkeypatch):
    """`classify_fetch` leaves a transient 5xx `blocked=False` while calling it
    "not genuine content" in the same breath — so ranking on `blocked` alone
    promotes a gateway error page to the rank meant for a usable page. It then
    clears the `not blocked` parse gate, and on a user preset with self_heal the
    regenerated selectors are persisted over the working ones — from a 503 body.
    """
    monkeypatch.setattr(settings, "page_task_timeout_s", 120.0)
    runner = _ScriptedRunner([_gateway_error_page(), _blocked_page()])
    _patch_session(monkeypatch, _session(2))

    out = await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 1},
    )

    assert isinstance(out, ScrapeOk)
    assert out.result.meta.status_code == 200, "the captcha, not the 503"
    assert "Captcha/block detected by heuristic" in out.result.warnings


@pytest.mark.asyncio
async def test_a_real_page_outranks_an_earlier_captcha(monkeypatch):
    """A page that is merely missing its selector beats an interstitial.

    Only the non-blocked page can be parsed at all (`apply_parser` is gated on
    `not blocked`) and it is the sole input self-heal can act on, so preferring
    the captcha would turn a recoverable selector drift into a blocked verdict —
    which the sibling law-checker copies straight into its scan state.
    """
    monkeypatch.setattr(settings, "page_task_timeout_s", 120.0)
    runner = _ScriptedRunner([_blocked_page(), _selector_miss_on_a_real_page()])
    _patch_session(monkeypatch, _session(2))

    out = await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 1},
    )

    assert isinstance(out, ScrapeOk)
    meta = out.result.meta
    assert meta.final_url == "https://ya.ru/search?text=x"
    assert "selector_not_found: li.serp-item" in out.result.warnings


@pytest.mark.asyncio
async def test_a_later_empty_failure_does_not_erase_the_block_evidence(monkeypatch):
    """The loop reports the attempt that explains the failure, not merely the last.

    A shortened last attempt often dies at the transport with no page at all;
    letting it overwrite an earlier captcha turns "this exit is burned" into
    "the connection failed", which routes the caller to the wrong remedy.
    """
    monkeypatch.setattr(settings, "page_task_timeout_s", 120.0)
    runner = _ScriptedRunner([_blocked_page(), _transport_failure(), _transport_failure()])
    _patch_session(monkeypatch, _session(3))

    out = await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 1},
    )

    assert runner.calls == 3, "every attempt still runs"
    assert isinstance(out, ScrapeOk)
    meta = out.result.meta
    assert meta.fetch_ok is False
    assert meta.status_code == 200, "the page that was actually fetched"
    assert "Captcha/block detected by heuristic" in out.result.warnings


@pytest.mark.asyncio
async def test_a_ceiling_too_small_for_any_attempt_still_answers(monkeypatch):
    """A misconfigured ceiling must produce a result slot, not a cancellation.

    The task is cancelled *inside* the fetch otherwise, and the caller is left
    with the generic stub — no url, no reason, `retries: 0`.
    """
    monkeypatch.setattr(settings, "page_task_timeout_s", 0.05)
    runner = _BudgetRunner(30_000)
    _patch_session(monkeypatch, _session(3))

    out = await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop", "proxy_type": "res_rotating"},
    )

    assert runner.budgets == [], "no exit should be spent on an attempt that cannot run"
    assert isinstance(out, ScrapeOk)
    assert out.result.meta.fetch_ok is False
    assert any("attempt 1/3 not started" in w for w in out.result.warnings)


@pytest.mark.asyncio
async def test_budget_notes_avoid_the_words_downstream_scans_for(monkeypatch):
    """yozh-law-checker re-crawls a scan when any scraper warning contains
    "timeout" or "goto" (`_seed_render_degenerate`). A rotation cut short by the
    ceiling is not a render fault, so naming the ceiling must not cost the caller
    a second crawl of the whole site."""
    monkeypatch.setattr(settings, "page_task_timeout_s", 1.0)
    runner = _BudgetRunner(400)
    _patch_session(monkeypatch, _session(3))

    out = await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 400},
    )

    assert isinstance(out, ScrapeOk)
    notes = [w for w in out.result.warnings if "attempt" in w]
    assert notes, "the run must actually have hit the ceiling"
    for note in notes:
        assert "timeout" not in note.lower(), note
        assert "goto" not in note.lower(), note


@pytest.mark.asyncio
async def test_an_unaffordable_next_attempt_is_not_rotated_into(monkeypatch):
    """Affordability is decided *before* the rotation, not after it.

    `on_failure` mints a fresh exit through the provider's live API; deciding
    afterwards that the attempt cannot run spends that exit on nothing — the same
    waste as rotating past the final attempt, one index earlier.
    """
    monkeypatch.setattr(settings, "page_task_timeout_s", 1.0)
    runner = _BudgetRunner(400)
    session = _session(3)
    _patch_session(monkeypatch, session)

    out = await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 400},
    )

    assert len(runner.budgets) == 2, f"third attempt should not run: {runner.budgets}"
    assert session.on_failure.await_count == 1, "only the rotation that was used"
    assert isinstance(out, ScrapeOk)
    assert any("not started" in w for w in out.result.warnings)


@pytest.mark.asyncio
async def test_the_exhausted_last_attempt_does_not_rotate(monkeypatch):
    """There is no attempt to rotate *into* after the last one.

    Rotating anyway asks the provider for a fresh exit nobody will use — a live
    API call per fully-blocked request — and logs "attempt 4/3", which is what
    made this visible while reading the budget guard's own output.
    """
    monkeypatch.setattr(settings, "page_task_timeout_s", 120.0)
    runner = _BudgetRunner(50)
    session = _session(2)
    _patch_session(monkeypatch, session)

    await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 50},
    )

    assert len(runner.budgets) == 2, "both attempts should still run"
    assert session.on_failure.await_count == 1, "rotated once, before attempt 2"


@pytest.mark.asyncio
async def test_the_caller_may_pass_the_deadline_it_is_actually_enforcing(monkeypatch):
    """The ceiling starts in `scrape_page`, not here.

    A session-pinned page waits on `store.lock(session_id)` first, whose
    acquire timeout is 65s — deriving the deadline from run_scrape's own start
    would put it that far past the real one, i.e. the guard would be a no-op on
    exactly the path that needs it most.
    """
    monkeypatch.setattr(settings, "page_task_timeout_s", 120.0)
    runner = _BudgetRunner(400)
    _patch_session(monkeypatch, _session(3))

    out = await run_scrape(
        runner, "req_budget",
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 400},
        None,
        # The caller started counting a minute ago, even though we start now.
        deadline=time.perf_counter() - 60,
    )

    assert runner.budgets == [], "a spent budget must be respected, not recomputed"
    assert isinstance(out, ScrapeOk)
    assert any("attempt 1/3 not started" in w for w in out.result.warnings)


class _RecordingRunner:
    """Records the budget it was handed and returns at once.

    Deliberately not `_BudgetRunner`: sleeping the granted budget to compare two
    integers made this the slowest test in the repo by an order of magnitude.
    """

    def __init__(self) -> None:
        self.budgets: list[int] = []

    async def fetch(self, *, timeout_ms: int | None = None, **_kw) -> FetchResult:
        self.budgets.append(timeout_ms)
        return _transport_failure()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra, phases",
    [
        ({"wait_for_selector": "li.x"}, 2),
        ({"warmup": {"type": "homepage", "dwell_ms": 0}}, 2),
        ({"wait_for_selector": "li.x", "warmup": {"type": "homepage", "dwell_ms": 0}}, 3),
    ],
)
async def test_the_phase_count_reaches_the_loop(monkeypatch, extra, phases):
    """The loop must hand the request's shape to the budget calculation.

    The division is unit-tested above, but nothing else notices if the loop stops
    passing `wait_for_selector` or `warmup` — the likeliest refactor to get
    wrong, since both are optional keywords, and dropping either silently
    restores the over-promise this whole change exists to remove.
    """
    monkeypatch.setattr(settings, "page_task_timeout_s", 100.0)
    # Far above the ceiling on purpose: with the configured value capping the
    # result, both runs would report `timeout_ms` and the division would be
    # invisible.
    base = {"url": "https://ya.ru/search", "device": "desktop",
            "proxy_type": "res_rotating", "timeout_ms": 10_000_000}
    _patch_session(monkeypatch, _session(1))

    plain = _RecordingRunner()
    await run_scrape(plain, "req_plain", dict(base), None, deadline=time.perf_counter() + 100)
    shaped = _RecordingRunner()
    await run_scrape(
        shaped, "req_shaped", {**base, **extra}, None, deadline=time.perf_counter() + 100,
    )

    assert shaped.budgets[0] == pytest.approx(plain.budgets[0] / phases, rel=0.02), (
        f"{extra} not charged as {phases} phases: {plain.budgets} vs {shaped.budgets}"
    )


def test_a_discarded_attempt_is_relayed_in_our_own_words():
    """Playwright spells the navigation deadline "Page.goto: Timeout ..." and
    appends a call log naming the URL. Both words are what yozh-law-checker
    scans `warnings` for to decide it should re-crawl the whole site — and this
    is the attempt whose page we threw away."""
    raw = (
        "PlaywrightError: Page.goto: Timeout 9581ms exceeded.\n"
        'Call log:\n  - navigating to "https://ya.ru/search?text=x", waiting until "load"\n'
    )

    summary = summarise_error(raw)

    assert summary == "the page did not finish loading in time"
    # A transport fault keeps its own name — it carries neither word.
    assert summarise_error(
        "PlaywrightError: net::ERR_TUNNEL_CONNECTION_FAILED at https://ya.ru/"
    ) == "net::ERR_TUNNEL_CONNECTION_FAILED at https://ya.ru/"


def test_a_call_log_on_the_first_line_does_not_ride_along():
    """The marker is spelled "Call log:" and the shared constant is lowercase.

    A single-line message therefore keeps the log — and with it the URL being
    crawled, which this module already documents as a thing downstream matchers
    misread (the needles are as short as three letters).
    """
    summary = summarise_error(
        'PlaywrightError: net::ERR_FAILED Call log: - navigating to '
        '"https://example.com/blog/proxy-timeout-guide", waiting until "load"'
    )

    assert summary == "net::ERR_FAILED"


def test_a_transport_fault_named_beside_the_deadline_survives():
    """`looks_like_proxy_failure` strips the deadline phrase and keeps the rest
    for exactly this reason; returning on the deadline discarded the evidence
    the relay exists to carry — "attempts 2-3 both failed to connect"."""
    summary = summarise_error(
        "TimeoutError: Page.goto: Timeout 30000ms exceeded. net::ERR_TUNNEL_CONNECTION_FAILED"
    )

    assert "net::ERR_TUNNEL_CONNECTION_FAILED" in summary
    assert "timeout" not in summary.lower() and "goto" not in summary.lower(), summary


def test_a_cut_message_says_it_was_cut():
    summary = summarise_error("net::ERR_WEIRD " + "x" * 400)

    assert summary.endswith("...")
    assert len(summary) == 120


@pytest.mark.asyncio
async def test_post_fetch_work_degrades_instead_of_killing_the_envelope(monkeypatch):
    """Parsing runs inside the same ceiling and can call an LLM.

    `parser_pipeline.run` makes up to two LLM calls (self-heal, then AI-only
    extraction), each bounded by PRESET_LLM_TIMEOUT_S=30s, and the markdown
    filter can make a third — none of which was ever budgeted. A fetch that ends
    near the ceiling therefore starts a 30s call against a few seconds of
    remaining budget, and the cancellation destroys the whole envelope: the page
    was fetched successfully and the caller still gets "page task exceeded".
    Losing the extraction is acceptable; losing the page is not.
    """
    monkeypatch.setattr(settings, "page_task_timeout_s", 1.0)

    async def _slow_parser(*_a, **_kw):
        await asyncio.sleep(30)
        return {"never": "returned"}, []

    monkeypatch.setattr("src.queue.scrape_runner.apply_parser", _slow_parser)
    runner = _ScriptedRunner([FetchResult(
        html="<html>content</html>", final_url="https://ya.ru/search", status_code=200,
        screenshot_b64=None, ok=True, error=None,
    )])
    _patch_session(monkeypatch, _session(1))

    out = await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop", "proxy_type": "res_rotating",
         "timeout_ms": 50, "extract": {"type": "css", "fields": {}}},
    )

    assert isinstance(out, ScrapeOk)
    assert out.result.meta.fetch_ok is True, "the page was fetched fine"
    # Pinned in both directions: the page survives, the extraction does not.
    assert out.result.data is None
    assert out.result.raw_html is None  # not requested; the page is in meta
    assert out.result.meta.status_code == 200
    assert any("preset parsing stopped" in w for w in out.result.warnings), (
        out.result.warnings
    )


@pytest.mark.asyncio
async def test_a_roomy_ceiling_changes_nothing(monkeypatch):
    """Regression guard: with budget to spare every attempt runs at the
    configured timeout, so the fix cannot quietly shorten healthy scrapes."""
    monkeypatch.setattr(settings, "page_task_timeout_s", 120.0)
    runner = _BudgetRunner(50)
    _patch_session(monkeypatch, _session(3))

    await _run_under_ceiling(
        runner,
        {"url": "https://ya.ru/search", "device": "desktop",
         "proxy_type": "res_rotating", "timeout_ms": 50},
    )

    assert runner.budgets == [50, 50, 50]
