"""The transport guard is only worth what its wiring is worth."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.browser.runner import PlaywrightRunner
from src.proxy.models import ProxyConfig
from src.settings import settings


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch):
    """This file is ABOUT the transport guard, so it runs with the flag on.
    `TestTheGuardIsOptIn` overrides it per-test — its own monkeypatch runs
    after this one and wins."""
    monkeypatch.setattr(settings, "egress_transport_guard", True)


def _runner() -> PlaywrightRunner:
    return PlaywrightRunner(headless=True, block_assets=False, timeout_ms=1000)


@pytest.mark.asyncio
async def test_the_direct_path_gets_a_guard_in_the_proxy_slot():
    """`proxy_type=none` is the DEFAULT, so this is the ordinary path — and it
    is the one that used to hand Playwright no proxy at all."""
    runner = _runner()
    effective, cm, guard = await runner.resolve_proxy(None)
    try:
        assert guard is not None
        assert effective is not None
        assert effective.server == guard.url
        assert effective.server.startswith("http://127.0.0.1:")
    finally:
        await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_the_proxied_path_is_untouched():
    """With an upstream proxy the browser already sends everything there, and
    inserting our listener would break every proxied scrape."""
    runner = _runner()
    upstream = ProxyConfig(server="http://proxy.example.com:8080")
    effective, cm, guard = await runner.resolve_proxy(upstream)
    assert guard is None
    assert cm is None
    assert effective is upstream


@pytest.mark.asyncio
async def test_the_guard_is_torn_down_with_the_bridge_slot():
    runner = _runner()
    _, cm, guard = await runner.resolve_proxy(None)
    url = guard.url
    await cm.__aexit__(None, None, None)
    host, port = url.removeprefix("http://").split(":")
    with pytest.raises((ConnectionRefusedError, OSError)):
        _, writer = await asyncio.open_connection(host, int(port))
        writer.close()


@pytest.mark.asyncio
async def test_the_context_actually_receives_the_guard(monkeypatch):
    """Catches 'built the guard, forgot to wire it' — the shape of bug this
    repo has shipped before."""
    runner = _runner()
    runner.start = AsyncMock()

    seen = {}
    context = AsyncMock()
    browser = AsyncMock()

    async def _new_context(**kwargs):
        seen.update(kwargs)
        return context

    browser.new_context = _new_context
    runner._browser = browser
    page = AsyncMock()
    page.url = "https://example.com/p"
    page.goto = AsyncMock(return_value=None)
    page.content = AsyncMock(return_value="<html>ok</html>")
    context.new_page = AsyncMock(return_value=page)
    monkeypatch.setattr("src.browser.runner.apply_page_masking", AsyncMock())

    await runner.fetch(
        url="https://example.com/p", device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=1000,
        screenshot=False,
    )

    # Playwright receives a dict, not a ProxyConfig — assert on what actually
    # reaches the browser.
    assert seen.get("proxy") is not None
    assert seen["proxy"]["server"].startswith("http://127.0.0.1:")


@pytest.mark.asyncio
async def test_refusals_reach_the_caller_as_a_warning(monkeypatch):
    """Without this a denied redirect hop surfaces as a raw transport error and
    reads like a broken proxy."""
    runner = _runner()
    runner.start = AsyncMock()

    context = AsyncMock()
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    runner._browser = browser
    page = AsyncMock()
    page.url = "https://example.com/p"
    page.content = AsyncMock(return_value="<html>ok</html>")
    context.new_page = AsyncMock(return_value=page)
    monkeypatch.setattr("src.browser.runner.apply_page_masking", AsyncMock())

    captured = {}
    real_resolve = runner.resolve_proxy

    async def _capture(proxy):
        effective, cm, guard = await real_resolve(proxy)
        captured["guard"] = guard
        if guard is not None:
            guard.denied.append("10.0.2.1:8000")
        return effective, cm, guard

    runner.resolve_proxy = _capture
    page.goto = AsyncMock(return_value=None)

    result = await runner.fetch(
        url="https://example.com/p", device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=1000,
        screenshot=False,
    )

    assert result.egress_denied == ["10.0.2.1:8000"]


class TestCamoufoxIsDeliberatelyNotTransportGuarded:
    """Pinning a decision, so the gap is visible rather than forgotten.

    Camoufox resolves the exit IP THROUGH whatever proxy it is handed
    (`camoufox/utils.py`: `public_ip(Proxy(**proxy).as_string())`) and caches
    that with `@lru_cache(maxsize=None)` keyed on the proxy string — verified
    by reading the installed package. The guard's URL carries a fresh port per
    fetch, so wiring it here would spend an external round trip to ipify on
    every fetch and leak one unbounded cache entry each time. Pre-resolving
    the IP instead makes an ipify outage fail the scrape before the browser
    launches.

    So Camoufox keeps the layers that need no proxy slot and still fires ONE
    internal GET on a redirect, where Chromium now fires none. If someone
    later wires it, this test goes red and they must update the note.
    """

    @pytest.mark.asyncio
    async def test_camoufox_gets_no_proxy_on_the_direct_path(self, monkeypatch):
        from src.browser import camoufox_runner as cfr

        seen = {}

        class _Ctx:
            async def __aenter__(self):
                browser = AsyncMock()
                page = AsyncMock()
                page.url = "https://example.com/p"
                page.context = AsyncMock()
                page.goto = AsyncMock(return_value=None)
                page.content = AsyncMock(return_value="<html>ok</html>")
                browser.new_page = AsyncMock(return_value=page)
                return browser

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(cfr, "AsyncCamoufox", lambda **o: (seen.update(o), _Ctx())[1])
        runner = cfr.CamoufoxRunner(timeout_ms=1000, headless=True)
        await runner.fetch(
            url="https://example.com/p", device="desktop", proxy=None, headers=None,
            wait_until="domcontentloaded", wait_for_selector=None,
            timeout_ms=1000, screenshot=False,
        )
        assert seen.get("proxy") is None
        # And `geoip` is untouched: the parameter exists for a future wiring,
        # but today it must still be exactly the configured setting.
        assert seen["geoip"] == settings.camoufox_geoip


class TestTheGuardIsOptIn:
    """Off by default, and the default is what every scrape gets.

    The guard is new network code on the critical path of `proxy_type=none`,
    and it changes four things there: a proxied Chromium context is TCP-only
    so HTTP/3 never negotiates (measured h2-vs-h3 against Cloudflare and
    Google), plain-HTTP forwards cost ~10x the connections, deploys on this
    host are a `git checkout` on a bind mount, and this repo has burned an exit
    pool on a fingerprint change before. So it ships dark and is turned on
    after an A/B against the preset suite, not before.
    """

    @pytest.mark.asyncio
    async def test_the_default_path_is_unchanged_when_the_flag_is_off(self, monkeypatch):
        monkeypatch.setattr(settings, "egress_transport_guard", False)
        runner = _runner()
        effective, cm, guard = await runner.resolve_proxy(None)
        assert (effective, cm, guard) == (None, None, None)

    def test_the_flag_defaults_to_on(self):
        """The DECLARED default, not the live value: this file forces it on and
        an .env could set it either way.

        It shipped OFF and was flipped after measurement. The A/B could not
        detect a block-rate difference — no target varied, so it had no
        discriminating power — but it did show that the only target which
        blocks at all blocks this host's IP with the guard OFF too. On the
        direct path, which is the only path this flag touches, the fingerprint
        objection protects a capability the host does not have.
        """
        from src.settings import Settings

        assert Settings.model_fields["egress_transport_guard"].default is True

    @pytest.mark.asyncio
    async def test_the_guard_appears_only_when_the_flag_is_on(self, monkeypatch):
        monkeypatch.setattr(settings, "egress_transport_guard", True)
        runner = _runner()
        effective, cm, guard = await runner.resolve_proxy(None)
        try:
            assert guard is not None
            assert effective.server == guard.url
        finally:
            await cm.__aexit__(None, None, None)


class TestTheProxiedPredicatesDoNotFlip:
    """`_new_context` names its parameter `proxy` but receives `effective_proxy`,
    so once the guard occupies that slot `proxy is None` inverted and the route
    guard silently dropped to `resolve=False` — two layers down to one, with no
    note anywhere. The same happened to the login path's `resolve_dns`."""

    @pytest.mark.asyncio
    async def test_the_route_guard_still_resolves_on_the_direct_path(self, monkeypatch):
        monkeypatch.setattr(settings, "egress_transport_guard", True)
        seen = {}

        def _spy(*, resolve):
            seen["resolve"] = resolve

            async def _noop(route):
                return None

            return _noop

        monkeypatch.setattr("src.browser.runner.make_route_guard", _spy)
        runner = _runner()
        runner.start = AsyncMock()
        context = AsyncMock()
        browser = AsyncMock()
        browser.new_context = AsyncMock(return_value=context)
        runner._browser = browser
        page = AsyncMock()
        page.url = "https://example.com/p"
        page.frames = ()
        page.goto = AsyncMock(return_value=None)
        page.content = AsyncMock(return_value="<html>ok</html>")
        context.new_page = AsyncMock(return_value=page)
        monkeypatch.setattr("src.browser.runner.apply_page_masking", AsyncMock())

        await runner.fetch(
            url="https://example.com/p", device="desktop", proxy=None, headers=None,
            wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=1000,
            screenshot=False,
        )
        assert seen["resolve"] is True, "the guard's own proxy inverted the predicate"


class TestARefusalIsNotAProxyFault:
    """A denied hop reached the caller as `ERR_TUNNEL_CONNECTION_FAILED`, and
    both "tunnel" and "net::err" are needles in `looks_like_proxy_failure` —
    so a deterministically-refused target rotated the exit and burned the whole
    retry budget, and the caller was told it was a proxy fault. That is exactly
    what `EGRESS_BLOCKED_ERROR` was made a constant to prevent, arriving
    through a different door.
    """

    def test_a_refused_target_does_not_trigger_rotation(self):
        from src.browser.runner import FetchResult
        from src.queue.scrape_runner import should_rotate_for

        refused = FetchResult(
            html="", final_url=None, status_code=None, screenshot_b64=None,
            ok=False, error="PlaywrightError: net::ERR_TUNNEL_CONNECTION_FAILED",
            egress_denied=["10.0.2.1:8000"],
        )
        assert should_rotate_for(refused) is False

    def test_an_ordinary_tunnel_failure_still_rotates(self):
        """The needle must keep working when the guard is NOT the cause —
        a genuinely broken exit looks identical on the wire."""
        from src.browser.runner import FetchResult
        from src.queue.scrape_runner import should_rotate_for

        broken = FetchResult(
            html="", final_url=None, status_code=None, screenshot_b64=None,
            ok=False, error="PlaywrightError: net::ERR_TUNNEL_CONNECTION_FAILED",
        )
        assert should_rotate_for(broken) is True

    def test_that_a_refusal_happened_reaches_the_caller(self):
        """The hostnames deliberately do NOT — see
        TestARefusalCannotForgeAVerdict."""
        from src.browser.runner import FetchResult
        from src.queue.scrape_runner import egress_warnings

        refused = FetchResult(
            html="", final_url=None, status_code=None, screenshot_b64=None,
            ok=False, error="x", egress_denied=["10.0.2.1:8000", "redis:6379"],
        )
        assert egress_warnings(refused) == ["egress_blocked: 2 non-public targets refused"]

    def test_nothing_is_added_when_nothing_was_refused(self):
        from src.browser.runner import FetchResult
        from src.queue.scrape_runner import egress_warnings

        clean = FetchResult(
            html="ok", final_url="https://example.com/", status_code=200,
            screenshot_b64=None, ok=True, error=None,
        )
        assert egress_warnings(clean) == []


class TestARefusalCannotForgeAVerdict:
    """A denied host is chosen by the TARGET, and `warnings` is read by a
    substring classifier in another repository.

    `yozh-law-checker/backend/app/worker/tasks.py:616` matches
    `("captcha", "block detected", "anti-bot", "antibot")` anywhere in a
    warning string, and treats a hit on the seed page as proof the scan was
    blocked. Copying a refused hostname in verbatim hands a target the lever:
    redirect a subresource to `captcha.example` that resolves privately, and
    the scan reports itself blocked. Found by the codex second pass — neither
    my own review nor the first reviewer crossed the repo boundary.
    """

    def test_the_warning_carries_no_target_supplied_text(self):
        from src.browser.runner import FetchResult
        from src.queue.scrape_runner import egress_warnings

        hostile = FetchResult(
            html="", final_url=None, status_code=None, screenshot_b64=None,
            ok=False, error="x",
            egress_denied=["captcha.example:443", "anti-bot.evil:80"],
        )
        warnings = egress_warnings(hostile)
        assert warnings, "a refusal must still be visible to the caller"
        joined = " ".join(warnings).lower()
        for marker in ("captcha", "block detected", "anti-bot", "antibot"):
            assert marker not in joined, f"{marker!r} reached the caller-visible warning"

    def test_the_warning_still_says_a_refusal_happened(self):
        from src.browser.runner import FetchResult
        from src.queue.scrape_runner import egress_warnings

        one = FetchResult(
            html="", final_url=None, status_code=None, screenshot_b64=None,
            ok=False, error="x", egress_denied=["10.0.2.1:8000"],
        )
        two = FetchResult(
            html="", final_url=None, status_code=None, screenshot_b64=None,
            ok=False, error="x", egress_denied=["10.0.2.1:8000", "redis:6379"],
        )
        assert egress_warnings(one) == ["egress_blocked: 1 non-public target refused"]
        assert egress_warnings(two) == ["egress_blocked: 2 non-public targets refused"]

    def test_nothing_is_said_when_nothing_was_refused(self):
        from src.browser.runner import FetchResult
        from src.queue.scrape_runner import egress_warnings

        clean = FetchResult(
            html="ok", final_url="https://e.com/", status_code=200,
            screenshot_b64=None, ok=True, error=None,
        )
        assert egress_warnings(clean) == []
