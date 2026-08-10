"""One page scrape: attempt loop + proxy rotation included.

This is the body of the old worker_pool._worker_main per-page path, moved
verbatim (zero logic changes). Transport-agnostic: give it a runner and a
request dict, get a ScrapeEnvelope back.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import traceback
from typing import Any, Coroutine, TypeVar

from pydantic import ValidationError

from src.browser.runner import (
    HTTP_BAN_STATUSES,
    HTTP_TRANSIENT_STATUSES,
    SELECTOR_MISS_PREFIX,
    FetchResult,
    PlaywrightRunner,
)
from src.markdown_build import apply as apply_markdown, resolve_formats
from src.queue.envelope import ScrapeEnvelope, ScrapeErr, ScrapeOk
from src.presets.models import PresetMeta
from src.queue.field_guards import (
    DEVICES,
    PROXY_TYPES,
    literal_or,
    model_or_none,
    str_or_none,
)
from src.schemas import (
    AppliedWarmup,
    ScrapeMeta,
    ScrapeResponse,
)
from src.presets.worker_parse import apply as apply_parser
from src.proxy.base import ProxyConfigError, ProxyFailure
from src.proxy.resolver import proxy_resolver
from src.settings import settings

log = logging.getLogger(__name__)


# HTTP status codes that indicate a proxy-related failure. Canonical sets live
# in browser.runner (the runner uses them to mark such a fetch not-ok); here we
# reuse them so the retry policy and the fetch verdict can't drift apart.
_RETRYABLE_HTTP_STATUSES = HTTP_BAN_STATUSES | HTTP_TRANSIENT_STATUSES

# `scrape_page` runs this whole function under asyncio.wait_for(PAGE_TASK_TIMEOUT_S),
# and a cancelled coroutine returns nothing: the block verdict, the html and the
# retry count are all destroyed and the caller gets a bare "page task exceeded".
# So the loop sizes every attempt against the same ceiling instead of ignoring it.
#
# Slack the attempt loop may not spend. It covers two things the phase model
# does not: packing the result after the loop, and the per-attempt cost no
# `timeout_ms` governs — the fresh Camoufox launch inside every fetch (which
# yandex_search asks for), `read_content_settling_navigation`'s retries, the
# full-page screenshot scroll, teardown. The expensive post-fetch work (parser
# and markdown, both of which can call an LLM) is bounded explicitly by
# `within_deadline` rather than reserved for, so a preset request does not pay
# up front for a self-heal it will usually not make. A fraction rather than a
# constant because the ceiling is configurable and a constant tuned for 120s
# would starve a deliberately short one.
_ATTEMPT_SLACK_FRACTION = 0.15
_ATTEMPT_SLACK_MAX_S = 5.0
# Under this, a navigation cannot even reach the target — spending an exit on it
# would burn a proxy to learn nothing, so the loop stops and reports what it has.
_MIN_ATTEMPT_TIMEOUT_MS = 100

# Residential rotating proxy types whose exit country is otherwise random when
# no proxy_geo.country_code is pinned. We default their country so the exit and
# the browser timezone/locale stay aligned (see settings.default_proxy_country).
# Mobile pools (mobile / mobile_shared) are intentionally excluded — their exit
# country is set by the provider's pool, not steered by country_code here.
_COUNTRY_DEFAULTED_PROXY_TYPES = frozenset({"res_rotating", "prem_res_rotating"})


def apply_default_proxy_country(
    proxy_type: str | None, proxy_geo: dict[str, str] | None
) -> dict[str, str] | None:
    """Pin a default country for residential rotating proxies with no country.

    Keeps the proxy exit and the browser fingerprint (timezone/locale, derived
    from the same proxy_geo via geo_profile) in the same country. An explicit
    country_code always wins; other proxy types and pinned requests pass through.
    """
    if proxy_type not in _COUNTRY_DEFAULTED_PROXY_TYPES:
        return proxy_geo
    if proxy_geo and proxy_geo.get("country_code"):
        return proxy_geo
    country = settings.default_proxy_country
    log.debug("defaulted %s exit country to %s (no proxy_geo.country_code)", proxy_type, country)
    return {**(proxy_geo or {}), "country_code": country}


# Playwright appends a call log naming the URL it was navigating to. The
# needles below are as short as three letters, so matching them against the
# whole message reads the *path being crawled* — /companies/dns/, /proxy-guide —
# as a transport fault.
_CALL_LOG_MARKER = "call log:"
# The exception class name each runner prefixes: PlaywrightRunner writes a
# literal "PlaywrightError: ", camoufox_runner uses type(exc).__name__. Playwright
# names its timeout class TimeoutError, which puts a needle in the prefix itself.
# Matched by name rather than \w*error so a class that *is* the evidence
# (ProxyError, DNSError) keeps classifying as a proxy failure.
_ERROR_CLASS_PREFIX_RE = re.compile(r"^(?:playwrighterror|timeouterror|unexpectederror): ")
# Playwright's navigation-budget expiry. Deliberately anchored to page.goto:
# Page.wait_for_selector's deadline is a different signal (for SERP presets a
# missing selector usually means a block) and must keep rotating.
_NAVIGATION_DEADLINE_RE = re.compile(r"page\.goto: timeout \d+ms exceeded\.?")

# These shapes are pinned to playwright==1.57 wording; re-verify on a bump.


def is_navigation_deadline(error: str | None) -> bool:
    """The page did not finish loading inside its budget.

    Says nothing about the exit's health: a proxy that accepts the connection
    and then hangs — or answers 407 to an HTTPS CONNECT, which is how expired
    or exhausted credentials surface — produces a byte-identical message.
    """
    if not error:
        return False
    return bool(_NAVIGATION_DEADLINE_RE.search(error.lower()))


def is_selector_miss(error: str | None) -> bool:
    """The page loaded but never grew the selector the request asked for.

    On a SERP preset that usually means an interstitial replaced the results,
    so the exit is worth rotating. Matched explicitly rather than by the word
    "timeout" leaking into the proxy-failure needles, which is how the runners'
    old raw `PlaywrightError` text reached the same decision by accident.
    """
    return error is not None and error.startswith(SELECTOR_MISS_PREFIX)


def looks_like_proxy_failure(status_code: int | None, error: str | None) -> bool:
    if status_code is not None and status_code in _RETRYABLE_HTTP_STATUSES:
        return True
    if not error:
        return False
    error = error.lower().split(_CALL_LOG_MARKER, 1)[0]
    error = _ERROR_CLASS_PREFIX_RE.sub("", error)
    # Drop a bare navigation deadline: on its own it means the page was slow,
    # and rotating on every attempt spends the whole budget three times over.
    # It is not proof the exit is healthy (see is_navigation_deadline), so the
    # caller grants one rotation for the recoverable cases; this only stops the
    # deadline from being retried to exhaustion. Removing the phrase rather
    # than returning early keeps a transport fault named alongside it retryable.
    error = _NAVIGATION_DEADLINE_RE.sub("", error)
    needles = (
        "proxy",
        "tunnel",
        "timed out",
        "timeout",
        "econnreset",
        "econnrefused",
        "enotfound",
        "dns",
        "net::err",
        "ns_error_",  # Firefox (Camoufox) network-error codes
        "connection closed",
        "socket hang up",
        "tls",
        "handshake",
    )
    return any(needle in error for needle in needles)


# Enough of a relayed error to recognise it; the reported attempt's own error is
# still carried verbatim.
_SUMMARY_CHARS = 120


def summarise_error(error: str) -> str:
    """A discarded attempt's failure, said in one line and in our own words.

    Playwright's raw text is a paragraph with a call log naming the URL, and it
    spells the navigation deadline as "Page.goto: Timeout ...". Relaying that for
    an attempt we are NOT reporting hands a downstream reader the words it scans
    for — yozh-law-checker re-crawls a whole site when "timeout" or "goto" shows
    up in `warnings` — on the strength of an attempt whose page was thrown away.
    The classification is what a consumer acts on, so relay that.
    """
    head = error.split("\n", 1)[0]
    # Both shared patterns are lowercase-only (their other caller folds the whole
    # message first), and Playwright writes "Call log:" — so fold a copy to find
    # the cut and slice the original. Without this the call log survives on the
    # rare single-line message, carrying the crawled URL into a field that is
    # pattern-matched downstream.
    cut = head.lower().find(_CALL_LOG_MARKER)
    if cut != -1:
        head = head[:cut]
    prefix = _ERROR_CLASS_PREFIX_RE.match(head.lower())
    if prefix:
        head = head[prefix.end():]
    # Remove the deadline phrase rather than returning on it, the way
    # `looks_like_proxy_failure` does: a transport fault named alongside it is
    # exactly the "attempts 2-3 both failed to connect" evidence this relay
    # exists to surface, and returning early threw it away.
    deadline = is_navigation_deadline(error)
    if deadline:
        head = re.sub(_NAVIGATION_DEADLINE_RE.pattern, "", head, flags=re.IGNORECASE)
    rest = " ".join(head.split()).strip(" .;,-")
    if deadline:
        phrase = "the page did not finish loading in time"
        rest = f"{phrase}; {rest}" if rest else phrase
    if not rest:
        return "failed"
    # Marked when cut, or a truncated message reads as a complete one.
    return rest if len(rest) <= _SUMMARY_CHARS else rest[: _SUMMARY_CHARS - 3] + "..."


def affordable_attempt_timeout_ms(
    remaining_s: float,
    configured_ms: int,
    *,
    wait_for_selector: str | None = None,
    warmup: dict[str, Any] | None = None,
) -> int | None:
    """The per-navigation budget this attempt may promise, or None if it cannot.

    `timeout_ms` is not the cost of an attempt — the runners apply it to *each*
    phase they perform: the optional warmup navigation, the real `page.goto`, and
    `wait_for_selector`. The Yandex preset asks for all three at 45 s, so one
    attempt can cost 137 s against a 120 s ceiling: it is cancelled mid-flight and
    everything it learned is lost. Dividing by the phase count is what makes the
    promise honest.

    Returning a *smaller* timeout is preferred over refusing the attempt: a
    shortened navigation that fails still comes back classified (blocked, HTTP
    status, selector miss), which is strictly more than a cancellation yields.
    """
    phases = 1 + (1 if wait_for_selector else 0) + (1 if warmup else 0)
    dwell_ms = 0
    if warmup:
        dwell = warmup.get("dwell_ms")
        dwell_ms = settings.warmup_dwell_ms if dwell is None else int(dwell)
    affordable = int((remaining_s * 1000 - dwell_ms) / phases)
    if affordable < _MIN_ATTEMPT_TIMEOUT_MS:
        return None
    return min(configured_ms, affordable)


_T = TypeVar("_T")


async def within_deadline(
    work: Coroutine[Any, Any, _T],
    *,
    deadline: float,
    label: str,
    warnings: list[str],
    default: _T,
) -> _T:
    """Run post-fetch work inside what is left of the task ceiling.

    Everything after the attempt loop — the preset parser (up to two LLM calls at
    PRESET_LLM_TIMEOUT_S each) and the markdown filter (a third) — runs inside the
    same `asyncio.wait_for` the whole task does, and none of it was ever budgeted.
    A fetch that ends near the ceiling would start a 30 s call against a few
    seconds of budget and the cancellation destroys the envelope: a page that was
    fetched perfectly well comes back as "page task exceeded". Losing the
    extraction is a degradation the caller can see and act on; losing the page is
    not.

    Bounds only work that yields: on the plain /scrape path `apply_parser` has no
    await points, so a pathological lxml parse still runs to completion. It is a
    coroutine, not any awaitable — a Task would keep running past the ceiling
    instead of being cancelled with it.
    """
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        # Never awaited, so close it explicitly rather than leaving a
        # "coroutine was never awaited" warning behind.
        getattr(work, "close", lambda: None)()
        warnings.append(f"{label} skipped: the page-task ceiling was already reached")
        return default
    try:
        return await asyncio.wait_for(work, timeout=remaining)
    except asyncio.TimeoutError:
        log.warning("%s stopped at the page-task ceiling (%.1fs budget)", label, remaining)
        warnings.append(f"{label} stopped at the page-task ceiling")
        return default


def _evidence_rank(result: FetchResult) -> int:
    """How much a failed attempt explains, highest first.

    A page that merely lacks its selector outranks an interstitial: it is the
    only one `apply_parser` will touch (parsing is gated on `not blocked`) and
    the only input self-heal can regenerate selectors from. An interstitial in
    turn outranks nothing at all, because "this exit is burned" and "the
    connection failed" send the caller to different remedies.

    `blocked` alone is not enough to tell the first rank from the second.
    `classify_fetch` leaves a transient 5xx `blocked=False` while calling it "not
    genuine content" in the same docstring, and a gateway error page has a
    perfectly good body — so ranking on `blocked` promotes it above a real
    captcha, hides the block from the caller, and clears the parse gate for a
    self-heal that would regenerate selectors from an error page and persist
    them over the working ones.
    """
    if not result.html:
        return 0
    if result.blocked or result.status_code in _RETRYABLE_HTTP_STATUSES:
        return 1
    return 2


def better_failure_evidence(
    candidate: FetchResult, incumbent: FetchResult | None
) -> FetchResult:
    """Of two failed attempts, the one that actually explains the failure.

    Reporting the *last* attempt is only a convention, and a costly one once
    attempts are shortened to fit the deadline: the tail attempt frequently dies
    at the transport with no page at all, and letting it overwrite an earlier
    captcha loses the verdict. Ties go to the later attempt — it was fetched on
    the exit the rotation moved to, so it describes the current state.
    """
    if incumbent is None:
        return candidate
    return candidate if _evidence_rank(candidate) >= _evidence_rank(incumbent) else incumbent


async def run_scrape(
    runner: PlaywrightRunner,
    request_id: str,
    request: dict[str, Any],
    storage_state: dict[str, Any] | None,
    *,
    deadline: float | None = None,
) -> ScrapeEnvelope:
    """Returns a ScrapeEnvelope: ScrapeOk carrying the ScrapeResponse that
    lands in the job's result slot, or ScrapeErr with a message (and a
    traceback only for an unexpected crash).

    `deadline` is a `time.perf_counter()` value: when the caller's own ceiling
    expires. It belongs to the caller because the ceiling starts there — a
    session-pinned page waits on the session lock (up to 65s) before this
    function is even entered, and deriving the deadline here would place it that
    far past the real one. Defaults to this call's own start plus
    PAGE_TASK_TIMEOUT_S, which is right for a caller that enforces nothing else.
    """
    start_time = time.perf_counter()
    task_deadline = (
        start_time + settings.page_task_timeout_s if deadline is None else deadline
    )

    proxy_type_raw = request.get("proxy_type")
    proxy_pool_id = request.get("proxy_pool_id")
    proxy_geo_data = request.get("proxy_geo")
    url = str(request.get("url"))

    proxy_geo: dict[str, str] | None = None
    if proxy_geo_data:
        proxy_geo = {
            "country_code": proxy_geo_data.get("country_code"),
            "region": proxy_geo_data.get("region"),
            "city": proxy_geo_data.get("city"),
        }
        proxy_geo = {k: v for k, v in proxy_geo.items() if v is not None}
        if not proxy_geo:
            proxy_geo = None

    proxy_geo = apply_default_proxy_country(proxy_type_raw, proxy_geo)

    log.info(
        "job received request_id=%s proxy_type=%s proxy_pool_id=%s url=%s",
        request_id,
        proxy_type_raw,
        proxy_pool_id,
        url,
    )

    try:
        session = await proxy_resolver.open_session(
            proxy_type=proxy_type_raw,
            proxy_pool_id=proxy_pool_id,
            proxy_geo=proxy_geo,
            max_retries=request.get("max_retries"),
            prem_proxy_options=request.get("prem_proxy_options"),
        )

        attempts = session.max_attempts()
        fetch_result: FetchResult | None = None
        # The attempt loop decides on the attempt it just made; what gets
        # reported is the attempt that best explains the outcome. They diverge
        # once a shortened tail attempt fails with no page (see
        # better_failure_evidence).
        reported_result: FetchResult | None = None
        retries_used = 0
        # Every way the ceiling changed what the loop did, in order. Silent
        # degradation is how a shortened attempt gets blamed on the target.
        budget_notes: list[str] = []
        # The ceiling scrape_page enforces from outside, minus what packing the
        # result still needs after the loop.
        attempt_deadline = task_deadline - min(
            _ATTEMPT_SLACK_MAX_S,
            settings.page_task_timeout_s * _ATTEMPT_SLACK_FRACTION,
        )

        # raw_html / screenshot can be requested via the legacy
        # booleans OR by listing them in `formats` (union). Resolve
        # once so capture and output agree.
        effective_formats = resolve_formats(
            request.get("formats"),
            raw_html=request.get("raw_html", False),
            screenshot=request.get("screenshot", False),
        )
        want_raw_html = "raw_html" in effective_formats
        want_screenshot = "screenshot" in effective_formats

        configured_timeout_ms = request.get("timeout_ms") or settings.request_timeout_ms

        def _affordable_now() -> int | None:
            """What the attempt starting right now may promise per navigation."""
            return affordable_attempt_timeout_ms(
                attempt_deadline - time.perf_counter(),
                configured_timeout_ms,
                wait_for_selector=request.get("wait_for_selector"),
                warmup=request.get("warmup"),
            )

        # Deliberately free of the words a downstream consumer scans for
        # ("timeout", "goto"): a truncated rotation is not a render fault, and
        # yozh-law-checker re-crawls with a different wait strategy when it sees
        # those in `warnings`.
        def _not_started(attempt_no: int) -> str:
            return (
                f"attempt {attempt_no}/{attempts} not started: the page-task "
                f"ceiling ({settings.page_task_timeout_s}s) left no budget for it"
            )

        for attempt in range(1, attempts + 1):
            proxy_cfg = session.current_proxy()
            log.debug(
                "attempt %d/%d for request_id=%s with proxy=%s",
                attempt,
                attempts,
                request_id,
                proxy_cfg.server if proxy_cfg else "none"
            )
            timeout_ms = _affordable_now()
            if timeout_ms is None:
                # Normally attempt 1 (a ceiling too small for any attempt at
                # all): later attempts are vetted before the rotation that leads
                # to them. It can still fire past attempt 1 when `on_failure`
                # itself — a live provider call — spends the slack that was
                # there when it was checked.
                budget_notes.append(_not_started(attempt))
                log.warning("%s (request_id=%s)", budget_notes[-1], request_id)
                break
            if timeout_ms < configured_timeout_ms:
                budget_notes.append(
                    f"attempt {attempt}/{attempts} shortened to {timeout_ms}ms of the "
                    f"requested {configured_timeout_ms}ms to fit the page-task ceiling"
                )
                log.info("%s (request_id=%s)", budget_notes[-1], request_id)
            log.debug(
                "fetching with timeout_ms=%d, wait_until=%s",
                timeout_ms,
                request.get("wait_until", "domcontentloaded")
            )

            fetch_result = await runner.fetch(
                url=request["url"],
                device=request.get("device", "desktop"),
                proxy=proxy_cfg,
                headers=request.get("headers"),
                wait_until=request.get("wait_until", "domcontentloaded"),
                wait_for_selector=request.get("wait_for_selector"),
                timeout_ms=timeout_ms,
                screenshot=want_screenshot,
                element_selector=request.get("element_selector"),
                stealth=request.get("stealth", True),
                block_assets=request.get("block_assets"),
                proxy_geo=proxy_geo,
                render=request.get("render", True),
                cookies=request.get("cookies"),
                storage_state=storage_state,
                viewport=request.get("viewport"),
                # Camoufox premium options; accepted-and-ignored by PlaywrightRunner
                humanize=request.get("humanize", False),
                spoof_os=request.get("spoof_os"),
                fingerprint_profile=request.get("fingerprint_profile"),
                block_webgl=request.get("block_webgl", False),
                addons=request.get("addons"),
                warmup=request.get("warmup"),
            )

            reported_result = (
                fetch_result if fetch_result.ok
                else better_failure_evidence(fetch_result, reported_result)
            )

            # If it is success - exit from cycle
            if fetch_result.ok:
                log.info(
                    "fetch succeeded on attempt %d for request_id=%s",
                    attempt,
                    request_id
                )
                break

            # If is not success - check, should rotate proxy
            log.warning(
                "fetch failed on attempt %d for request_id=%s: %s",
                attempt,
                request_id,
                fetch_result.error
            )

            # Rotate + retry on a proxy/network failure OR a captcha/block: a
            # block means this IP is burned, so a fresh proxy is the fix (bounded
            # by max_attempts). Anything else is a genuine target response — don't
            # waste proxies retrying it.
            should_rotate = (
                fetch_result.blocked
                or is_selector_miss(fetch_result.error)
                or looks_like_proxy_failure(
                    fetch_result.status_code, fetch_result.error
                )
            )
            # A navigation deadline is not evidence of a bad exit, but it is not
            # evidence of a good one either — a hung exit, or one answering 407
            # because the credentials expired, looks exactly like a slow page.
            # Grant exactly one rotation: `recover()` mints fresh credentials, so
            # this keeps the recoverable cases recoverable, while still ending the
            # runaway where all three attempts burned the full budget in a row.
            deadline_retry = (
                not should_rotate
                and attempt == 1
                and attempts > 1
                and is_navigation_deadline(fetch_result.error)
            )
            if not (should_rotate or deadline_retry):
                if is_navigation_deadline(fetch_result.error):
                    log.warning(
                        "navigation deadline after %d attempt(s), giving up: "
                        "request_id=%s url=%s timeout_ms=%d exit=%s — the page "
                        "did not load in time; if this repeats across targets, "
                        "suspect the exit",
                        attempt,
                        request_id,
                        request.get("url"),
                        timeout_ms,
                        proxy_cfg.server if proxy_cfg else "none",
                    )
                else:
                    log.info(
                        "error is neither proxy failure nor block, "
                        "stopping retries for request_id=%s",
                        request_id
                    )
                break

            # Increment retry counter
            retries_used = attempt

            # `on_failure` mints a fresh exit through the provider's live API, so
            # both reasons the next attempt cannot happen have to be settled
            # BEFORE it runs — otherwise the exit is bought and thrown away.
            if attempt == attempts:
                log.info(
                    "attempts exhausted (%d/%d) for request_id=%s", attempt, attempts, request_id
                )
                break
            if _affordable_now() is None:
                budget_notes.append(_not_started(attempt + 1))
                log.warning("%s (request_id=%s)", budget_notes[-1], request_id)
                break

            # Call on_failure for proxy rotation
            should_retry = await session.on_failure(
                ProxyFailure(
                    status_code=fetch_result.status_code,
                    error=fetch_result.error
                )
            )

            if not should_retry:
                log.warning(
                    "session.on_failure returned False, stopping retries for request_id=%s",
                    request_id
                )
                break

            log.info(
                "rotating to next proxy for request_id=%s, attempt %d/%d",
                request_id,
                attempt + 1,
                attempts
            )

        # --- Render result ---
        warnings: list[str] = []
        data = None

        # From here on the reported attempt is the subject: meta, warnings, the
        # parsed data and the storage_state all describe the page being returned.
        last_result = fetch_result
        fetch_result = reported_result

        if fetch_result is None:
            warnings.append("fetch_result_is_none")
            fetch_result = FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error="No fetch result"
            )

        if not fetch_result.ok:
            if fetch_result.error:
                warnings.append(fetch_result.error)
            if fetch_result.status_code:
                warnings.append(f"status_code={fetch_result.status_code}")
        # The last attempt's error when it is not the one being reported: the
        # reported page explains the failure, but "attempts 2-3 both failed to
        # connect" is how a broken pool becomes visible, and dropping it makes
        # three dead exits read as a plain captcha.
        if last_result is not None and last_result is not fetch_result and last_result.error:
            warnings.append(f"last attempt: {summarise_error(last_result.error)}")
        # Say so when the rotation budget was cut short rather than exhausted:
        # otherwise "blocked after 2 attempts" and "blocked after 2 of 3 attempts,
        # the third never affordable" are indistinguishable to the caller.
        warnings.extend(budget_notes)

        # Parse data: preset pipeline (self-heal / AI) when a
        # parser_plan is present, else the plain deterministic
        # extract for raw /scrape callers.
        # `blocked` gates this, not html emptiness: an interstitial has a
        # perfectly good DOM, and self-heal would regenerate selectors from
        # it — an LLM call per blocked request, and for a user preset those
        # regenerated selectors are persisted over the working ones.
        if (
            (request.get("extract") or request.get("parser_plan"))
            and fetch_result.html
            and not fetch_result.blocked
        ):
            parsed_data, parse_warnings = await within_deadline(
                apply_parser(
                    fetch_result.html,
                    request.get("extract"),
                    request.get("parser_plan"),
                ),
                deadline=attempt_deadline,
                label="preset parsing",
                warnings=warnings,
                default=(None, []),
            )
            data = parsed_data
            warnings.extend(parse_warnings)

        raw_html = fetch_result.html if want_raw_html else None
        screenshot_b64 = fetch_result.screenshot_b64 if want_screenshot else None

        # Markdown-family outputs (markdown / fit_markdown / html /
        # links). Best-effort: failures degrade to warnings, never
        # fail the scrape. No-op for legacy callers.
        markdown_outputs, md_warnings = await within_deadline(
            apply_markdown(
                request,
                fetch_result.html,
                base_url=fetch_result.final_url or url,
            ),
            deadline=attempt_deadline,
            label="markdown rendering",
            warnings=warnings,
            default=({}, []),
        )
        warnings.extend(md_warnings)

        took_ms = int((time.perf_counter() - start_time) * 1000)

        # Echo the resolved premium targeting (suffix only — no credentials) so
        # clients can see which country/session/filter actually hit the gateway.
        # Set only by the prem provider; None for every other proxy type.
        final_proxy = session.current_proxy()
        prem_targeting = final_proxy.targeting_suffix if final_proxy else None

        # Send result. Built as the pydantic models rather than a dict: the
        # same ScrapeResponse is what the API validates on the way out, so
        # constructing it here closes the loop instead of hand-rolling its
        # shape a second time.
        try:
            response = ScrapeResponse(
                request_id=request_id,
                took_ms=took_ms,
                meta=ScrapeMeta(
                    url=url,
                    final_url=fetch_result.final_url,
                    status_code=fetch_result.status_code,
                    device=literal_or(
                        request.get("device"), DEVICES, "desktop", field="device", sink=warnings
                    ),
                    proxy_type=literal_or(
                        proxy_type_raw, PROXY_TYPES, "none", field="proxy_type", sink=warnings
                    ),
                    proxy_pool_id=str_or_none(
                        proxy_pool_id, field="proxy_pool_id", sink=warnings
                    ),
                    retries=retries_used,
                    fetch_ok=fetch_result.ok,
                    applied_fingerprint=fetch_result.applied_fingerprint,
                    applied_user_agent=fetch_result.applied_user_agent,
                    applied_locale=fetch_result.applied_locale,
                    applied_timezone=fetch_result.applied_timezone,
                    applied_accept_language=fetch_result.applied_accept_language,
                    applied_preset=model_or_none(
                        PresetMeta, request.get("preset_meta"), field="applied_preset", sink=warnings
                    ),
                    applied_prem_targeting=prem_targeting,
                    applied_warmup=model_or_none(
                        AppliedWarmup, fetch_result.applied_warmup, field="applied_warmup",
                        sink=warnings,
                    ),
                ),
                data=data,
                raw_html=raw_html,
                markdown=markdown_outputs.get("markdown"),
                fit_markdown=markdown_outputs.get("fit_markdown"),
                markdown_references=markdown_outputs.get("markdown_references"),
                links=markdown_outputs.get("links"),
                html=markdown_outputs.get("html"),
                screenshot_base64=screenshot_b64,
                element_screenshot_status=fetch_result.element_status,
                warnings=warnings,
            )
        except ValidationError as exc:
            # A completed page whose metadata will not validate. Distinct and
            # greppable on purpose: this is NOT a browser failure, and the
            # generic worker-error line would hide that we threw away real data.
            log.error(
                "result_schema_rejected request_id=%s url=%s status=%s: %s",
                request_id, url, fetch_result.status_code, exc,
            )
            # The caller gets the field names, not pydantic's paragraph: this
            # lands in `ScrapeResponse.error` AND `warnings[0]`, and the raw text
            # carries `input_value=` — i.e. a slice of whatever was rejected —
            # plus internal paths and a docs URL into the public API surface.
            fields = sorted({
                ".".join(str(p) for p in err["loc"]) or "<root>"
                for err in exc.errors()
            })
            # The run's own request_id, not "see the log": `make_error_payload`
            # mints a fresh one for the slot, so the id the client receives and
            # the id in the log line are different strings with nothing joining
            # them.
            return ScrapeErr(
                error=(
                    f"result_schema_rejected: {', '.join(fields)} "
                    f"(request_id={request_id})"
                )
            )
        return ScrapeOk(result=response, storage_state=fetch_result.storage_state)
    except ProxyConfigError as e:
        # User input, not a code failure — WARNING without traceback.
        log.warning("proxy config error for request_id=%s: %s", request_id, e)
        # Same "TypeName: message" shape the session errors use in tasks.py.
        return ScrapeErr(error=f"{type(e).__name__}: {e}")

    except Exception as e:
        error_traceback = traceback.format_exc(limit=20)
        log.error(
            "unexpected error in worker for request_id=%s: %s\n%s",
            request_id,
            str(e),
            error_traceback,
        )
        # `or type(e).__name__` because the envelope no longer carries a
        # "worker_failed" default at the read side: an exception whose str() is
        # empty would otherwise surface as a blank message.
        return ScrapeErr(error=str(e) or type(e).__name__, traceback=error_traceback)
