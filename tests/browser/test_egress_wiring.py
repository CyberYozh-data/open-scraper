"""The policy is only worth what its wiring is worth.

Every test here fails if a call site is removed, which is the failure mode this
whole change exists to prevent: a correct predicate that nobody calls is the
state the repo was already in — `_assert_public_url` had exactly one caller and
four unguarded navigation sites.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.browser.runner import PlaywrightRunner, run_warmup
from src.proxy.models import ProxyConfig
from src.security.egress import EGRESS_BLOCKED_ERROR, EgressBlocked
from src.settings import settings


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "egress_allow_hosts", "")


def _runner() -> PlaywrightRunner:
    return PlaywrightRunner(headless=True, block_assets=False, timeout_ms=1000)


async def _fetch(runner: PlaywrightRunner, url: str, **kw):
    return await runner.fetch(
        url=url,
        device="desktop",
        proxy=kw.pop("proxy", None),
        headers=None,
        wait_until="domcontentloaded",
        wait_for_selector=None,
        timeout_ms=1000,
        screenshot=kw.pop("screenshot", False),
        **kw,
    )


@pytest.mark.asyncio
async def test_fetch_refuses_internal_target_without_starting_a_browser():
    """The pre-flight runs before `start()`, so a blocked target costs no
    browser launch and no proxy lease."""
    runner = _runner()
    runner.start = AsyncMock()
    runner.resolve_proxy = AsyncMock(return_value=(None, None))

    result = await _fetch(runner, "http://10.0.2.1:8000/admin")

    assert result.ok is False
    assert result.error == EGRESS_BLOCKED_ERROR
    assert result.html == ""
    runner.start.assert_not_awaited()
    runner.resolve_proxy.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_refuses_non_http_scheme():
    runner = _runner()
    runner.start = AsyncMock()

    result = await _fetch(runner, "file:///etc/passwd")

    assert result.ok is False
    assert result.error == EGRESS_BLOCKED_ERROR
    runner.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_proxied_fetch_still_blocks_a_loopback_literal():
    """`resolve=False` skips DNS on the proxied path — it does not skip the
    address check. A loopback literal in a proxied Camoufox context was
    measured to bypass the proxy entirely."""
    runner = _runner()
    runner.start = AsyncMock()

    result = await _fetch(
        runner,
        "http://127.0.0.1:9/",
        proxy=ProxyConfig(server="http://proxy.example.com:8080"),
    )

    assert result.ok is False
    assert result.error == EGRESS_BLOCKED_ERROR
    runner.start.assert_not_awaited()


async def _new_context_with_mocked_browser(runner: PlaywrightRunner, **kw):
    """Drive `_new_context` far enough to observe the route registrations."""
    context = AsyncMock()
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    runner._browser = browser
    await runner._new_context(device="desktop", proxy=None, headers=None, **kw)
    return context


@pytest.mark.asyncio
async def test_guard_is_registered_even_when_block_assets_is_false():
    """Zero `context.route` calls at HEAD when assets are not blocked — the
    guard must not inherit that switch."""
    runner = _runner()
    context = await _new_context_with_mocked_browser(runner, block_assets=False)

    patterns = [c.args[0] for c in context.route.await_args_list]
    assert patterns == ["**/*"]


@pytest.mark.asyncio
async def test_guard_is_registered_after_the_asset_blocker():
    """Playwright runs route handlers last-registered-first, so the guard has
    to be registered second to run first."""
    from src.browser.runner import _block_assets_route

    runner = _runner()
    context = await _new_context_with_mocked_browser(runner, block_assets=True)

    handlers = [c.args[1] for c in context.route.await_args_list]
    assert len(handlers) == 2
    assert handlers[0] is _block_assets_route
    assert handlers[1] is not _block_assets_route


@pytest.mark.asyncio
async def test_warmup_custom_url_to_private_is_refused_without_navigating():
    page = AsyncMock()
    outcome = await run_warmup(
        page,
        "https://example.com/target",
        {"type": "custom", "url": "http://169.254.169.254/latest/meta-data/"},
        timeout_ms=1000,
        default_dwell_ms=0,
    )
    page.goto.assert_not_awaited()
    assert outcome.applied is None
    assert outcome.error is not None


@pytest.mark.asyncio
async def test_warmup_homepage_of_a_public_target_still_runs():
    page = AsyncMock()
    page.url = "https://example.com/"
    page.goto = AsyncMock(
        return_value=MagicMock(request=MagicMock(redirected_from=None))
    )
    with patch("src.security.egress._getaddrinfo",
               lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 0))]):
        outcome = await run_warmup(
            page, "https://example.com/a/b", {"type": "homepage"},
            timeout_ms=1000, default_dwell_ms=0,
        )
    page.goto.assert_awaited_once()
    assert outcome.applied is not None


@pytest.mark.asyncio
async def test_camoufox_refuses_internal_target_before_launching():
    from src.browser import camoufox_runner as cfr

    runner = cfr.CamoufoxRunner(timeout_ms=1000, headless=True)
    sentinel = MagicMock(side_effect=AssertionError("Camoufox launched for a blocked target"))
    with patch.object(cfr, "AsyncCamoufox", sentinel):
        result = await runner.fetch(
            url="http://redis:6379/",
            device="desktop",
            proxy=None,
            headers=None,
            wait_until="domcontentloaded",
            wait_for_selector=None,
            timeout_ms=1000,
            screenshot=False,
        )

    assert result.ok is False
    assert result.error == EGRESS_BLOCKED_ERROR
    sentinel.assert_not_called()


def test_login_step_rejects_a_non_http_scheme():
    """`file://` makes no TCP connection, so no address check and no transport
    guard can ever see it. The schema is the only place it can be caught."""
    from src.sessions.models import LoginStep

    with pytest.raises(ValidationError):
        LoginStep(op="goto", url="file:///etc/passwd")


def test_login_step_still_accepts_an_http_url():
    from src.sessions.models import LoginStep

    assert LoginStep(op="goto", url="https://example.com/login").url == (
        "https://example.com/login"
    )


class TestLoginNavigationIsGuarded:
    """`/api/v1/sessions` is behind SERVICE_TOKEN, so this is an authenticated
    footgun rather than an anonymous hole — but the login goto had a scheme
    check and nothing else, while its failure path returns a screenshot of
    whatever it landed on. That is a full-read primitive, not a blind one.
    """

    @staticmethod
    def _step(url: str):
        from src.sessions.models import LoginStep

        return LoginStep.model_construct(op="goto", url=url)

    @pytest.mark.asyncio
    async def test_goto_to_an_internal_address_is_refused(self):
        from src.browser.login_runner import LoginRunner

        page = AsyncMock()
        with pytest.raises(EgressBlocked):
            await LoginRunner()._dispatch(page, self._step("http://10.0.2.1/admin"), {})
        page.goto.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_credential_substituted_scheme_is_refused(self):
        """The model validated the TEMPLATE; `$creds_` substitution happens
        after, so the value can carry its own scheme."""
        from src.browser.login_runner import LoginRunner

        page = AsyncMock()
        step = self._step("$creds_target")
        with pytest.raises(EgressBlocked):
            await LoginRunner()._dispatch(page, step, {"target": "file:///etc/passwd"})
        page.goto.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_public_goto_still_runs(self, monkeypatch):
        from src.browser.login_runner import LoginRunner

        monkeypatch.setattr(
            "src.security.egress._getaddrinfo",
            lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 0))],
        )
        page = AsyncMock()
        # A real Playwright page exposes `.url` as a str; the landing check
        # reads it, and an AsyncMock attribute is not one.
        page.url = "https://example.com/login"
        page.goto = AsyncMock(
            return_value=MagicMock(request=MagicMock(redirected_from=None))
        )
        await LoginRunner()._dispatch(page, self._step("https://example.com/login"), {})
        page.goto.assert_awaited_once()


class _FlippingPage:
    """A page whose `url` changes between `goto` and the content read.

    Needed because a mock whose url is internal from the start is caught by
    BOTH the landing check and the read-time re-check, so deleting either one
    leaves the suite green — measured. Each check has to be isolated by a
    scenario only it can catch.
    """

    def __init__(self, *, goto_url: str, settled_url: str, chain=None, html="<html>ok</html>"):
        self._goto_url = goto_url
        self._settled_url = settled_url
        self._chain = chain
        self._html = html
        self._settled = False
        self.context = AsyncMock()
        self.route = AsyncMock()
        self.set_extra_http_headers = AsyncMock()
        self.wait_for_timeout = AsyncMock()
        self.wait_for_selector = AsyncMock()
        self.evaluate = AsyncMock(return_value={})
        self.close = AsyncMock()

    @property
    def url(self) -> str:
        return self._settled_url if self._settled else self._goto_url

    async def goto(self, *_a, **_k):
        return MagicMock(status=200, request=MagicMock(redirected_from=self._chain))

    async def content(self) -> str:
        # Standing in for read_content_settling_navigation waiting out an
        # in-flight navigation and returning the new page's content.
        self._settled = True
        return self._html


async def _camoufox_fetch(monkeypatch, page, url="https://example.com/p"):
    from src.browser import camoufox_runner as cfr

    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)

    class _Ctx:
        async def __aenter__(self):
            return browser

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(cfr, "AsyncCamoufox", lambda **_k: _Ctx())
    runner = cfr.CamoufoxRunner(timeout_ms=1000, headless=True)
    return await runner.fetch(
        url=url, device="desktop", proxy=None, headers=None,
        wait_until="domcontentloaded", wait_for_selector=None,
        timeout_ms=1000, screenshot=False,
    )


class TestCamoufoxWiring:
    """Three call sites survived a mutation sweep with the whole suite green.
    These isolate them."""

    @pytest.mark.asyncio
    async def test_route_guard_is_registered_on_the_context_not_the_page(self, monkeypatch):
        """A popup or `target=_blank` window is a different page in the same
        context; a page-level handler would leave it unguarded."""
        page = _FlippingPage(goto_url="https://example.com/p", settled_url="https://example.com/p")
        await _camoufox_fetch(monkeypatch, page)
        page.context.route.assert_awaited_once()
        assert page.context.route.await_args.args[0] == "**/*"
        page.route.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_private_redirect_hop_is_refused(self, monkeypatch):
        """Only the landing check can catch this: `page.url` is public
        throughout, and the private address appears only in the chain."""
        hop = MagicMock()
        hop.url = "http://10.0.2.1/internal"
        hop.redirected_from = None
        page = _FlippingPage(
            goto_url="https://example.com/p",
            settled_url="https://example.com/p",
            chain=hop,
            html="<html>LEAKED</html>",
        )
        result = await _camoufox_fetch(monkeypatch, page)
        assert result.ok is False
        assert result.error == EGRESS_BLOCKED_ERROR
        assert "LEAKED" not in (result.html or "")

    @pytest.mark.asyncio
    async def test_a_page_that_navigates_internal_before_the_read_is_refused(self, monkeypatch):
        """Only the read-time re-check can catch this: the chain is clean and
        `page.url` was public when `goto` returned."""
        page = _FlippingPage(
            goto_url="https://example.com/p",
            settled_url="http://10.0.2.1/internal",
            html="<html>LEAKED</html>",
        )
        result = await _camoufox_fetch(monkeypatch, page)
        assert result.ok is False
        assert result.error == EGRESS_BLOCKED_ERROR
        assert "LEAKED" not in (result.html or "")

    @pytest.mark.asyncio
    async def test_a_wholly_public_fetch_is_unaffected(self, monkeypatch):
        page = _FlippingPage(goto_url="https://example.com/p", settled_url="https://example.com/p")
        result = await _camoufox_fetch(monkeypatch, page)
        assert result.ok is True
        assert result.html == "<html>ok</html>"


class TestLoginLandingCheck:
    @pytest.mark.asyncio
    async def test_a_private_redirect_hop_on_a_login_goto_is_refused(self):
        """The login page is built through `runner._new_context`, so it has the
        route guard — which is blind to redirect hops by construction."""
        from src.browser.login_runner import LoginRunner
        from src.sessions.models import LoginStep

        hop = MagicMock()
        hop.url = "http://10.0.2.1/internal"
        hop.redirected_from = None
        page = AsyncMock()
        page.url = "https://example.com/login"
        page.goto = AsyncMock(return_value=MagicMock(request=MagicMock(redirected_from=hop)))

        step = LoginStep.model_construct(op="goto", url="https://example.com/login")
        with pytest.raises(EgressBlocked):
            await LoginRunner()._dispatch(page, step, {})


class TestLoginRefusalReturnsNothing:
    """The comment on the login goto names `_try_screenshot` as the reason a
    scheme check was not enough — and then that primitive ran anyway.

    Measured: a refused step returned `ok=False` with the right error AND
    2,067,659 pixels of the internal page in `screenshot_b64`.
    """

    @pytest.mark.asyncio
    async def test_no_screenshot_is_returned_when_a_step_is_refused_for_egress(self):
        from src.browser.login_runner import LoginRunner
        from src.sessions.models import LoginScript, LoginStep

        page = AsyncMock()
        page.url = "https://example.com/login"
        page.screenshot = AsyncMock(return_value=b"PNG-OF-THE-INTERNAL-PAGE")
        script = LoginScript(
            steps=[LoginStep.model_construct(op="goto", url="http://10.0.2.1/admin")]
        )

        result, state = await LoginRunner().replay(
            page=page, context=AsyncMock(), script=script, creds={},
        )

        assert result.ok is False
        assert result.error == EGRESS_BLOCKED_ERROR
        assert result.screenshot_b64 is None, "returned an image of the refused page"
        assert state is None
        page.screenshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_ordinary_step_failure_still_returns_its_screenshot(self):
        """The diagnostic value of the screenshot is the whole point of the
        failure path; only the egress refusal withholds it."""
        from src.browser.login_runner import LoginRunner
        from src.sessions.models import LoginScript, LoginStep

        page = AsyncMock()
        page.url = "https://example.com/login"
        page.screenshot = AsyncMock(return_value=b"PNG")
        page.fill = AsyncMock(side_effect=RuntimeError("selector not found"))
        script = LoginScript(
            steps=[LoginStep(op="fill", selector="#user", value="x")]
        )

        result, _ = await LoginRunner().replay(
            page=page, context=AsyncMock(), script=script, creds={},
        )

        assert result.ok is False
        assert result.screenshot_b64 is not None


class TestScreenshotWindowIsGuardedOnBothEngines:
    """`_fullpage_screenshot` scrolls up to 200 times at 120 ms, so the capture
    is a ~24 s window in which the page can navigate. Chromium re-checked after
    it; Camoufox did not, which made the hole engine-dependent."""

    @staticmethod
    def _page_that_navigates_during_capture(capture_url: str):
        page = AsyncMock()
        page.url = "https://example.com/p"
        page.frames = ()
        page.goto = AsyncMock(
            return_value=MagicMock(status=200, request=MagicMock(redirected_from=None))
        )
        page.content = AsyncMock(return_value="<html>ok</html>")
        page.context = AsyncMock()

        async def _screenshot(*_a, **_k):
            page.url = capture_url
            return b"PNG"

        page.screenshot = _screenshot
        return page

    @pytest.mark.asyncio
    async def test_chromium_refuses_a_page_that_navigated_during_capture(self, monkeypatch):
        runner = _runner()
        runner.start = AsyncMock()
        page = self._page_that_navigates_during_capture("http://10.0.2.1/internal")
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser = AsyncMock()
        browser.new_context = AsyncMock(return_value=context)
        runner._browser = browser
        monkeypatch.setattr("src.browser.runner.apply_page_masking", AsyncMock())

        result = await _fetch(runner, "https://example.com/p", screenshot=True)

        assert result.ok is False
        assert result.error == EGRESS_BLOCKED_ERROR
        assert result.screenshot_b64 is None

    @pytest.mark.asyncio
    async def test_camoufox_refuses_a_page_that_navigated_during_capture(self, monkeypatch):
        from src.browser import camoufox_runner as cfr

        page = self._page_that_navigates_during_capture("http://10.0.2.1/internal")
        browser = AsyncMock()
        browser.new_page = AsyncMock(return_value=page)

        class _Ctx:
            async def __aenter__(self):
                return browser

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(cfr, "AsyncCamoufox", lambda **_k: _Ctx())
        runner = cfr.CamoufoxRunner(timeout_ms=1000, headless=True)
        result = await runner.fetch(
            url="https://example.com/p", device="desktop", proxy=None, headers=None,
            wait_until="domcontentloaded", wait_for_selector=None,
            timeout_ms=1000, screenshot=True,
        )

        assert result.ok is False
        assert result.error == EGRESS_BLOCKED_ERROR
        assert result.screenshot_b64 is None


@pytest.mark.asyncio
async def test_chromium_refuses_a_private_hop_in_an_otherwise_public_chain(monkeypatch):
    """The Chromium landing check had no test at all: the read-time check now
    shadows the simple redirect case, so only a chain that ENDS public can
    isolate it. Deleting the call made the whole gate stay green."""
    runner = _runner()
    runner.start = AsyncMock()

    hop = MagicMock()
    hop.url = "http://10.0.2.1/internal"
    hop.redirected_from = None
    page = AsyncMock()
    page.url = "https://example.com/final"
    page.frames = ()
    page.goto = AsyncMock(
        return_value=MagicMock(status=200, request=MagicMock(redirected_from=hop))
    )
    page.content = AsyncMock(return_value="<html>LEAKED</html>")
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    runner._browser = browser
    monkeypatch.setattr("src.browser.runner.apply_page_masking", AsyncMock())

    result = await _fetch(runner, "https://example.com/start")

    assert result.ok is False
    assert result.error == EGRESS_BLOCKED_ERROR
    assert "LEAKED" not in (result.html or "")


@pytest.mark.parametrize(
    "proxy_server, expected_resolve_dns",
    [("http://proxy.example:8080", False), (None, True)],
)
@pytest.mark.asyncio
async def test_login_threads_resolve_dns_from_the_effective_proxy(
    monkeypatch, proxy_server, expected_resolve_dns
):
    """Drop the kwarg and `resolve_dns` silently defaults to True, so every
    PROXIED login starts resolving against local DNS — which describes a
    different network than the proxy will dial. Nothing went red before this."""
    import src.queue.tasks as tasks_mod
    from src.proxy.models import ProxyConfig
    from src.sessions.models import SessionLoginResult

    seen = {}
    effective = ProxyConfig(server=proxy_server) if proxy_server else None

    class _Runner:
        _engine = "chromium"
        start = AsyncMock()

        async def resolve_proxy(self, _proxy):
            # Three values since the transport guard landed: (proxy, bridge_cm,
            # guard). The `None` case models the flag being OFF, which is the
            # shipped default — with it on, the real `resolve_proxy` hands the
            # login context the guard's own proxy, and `resolve_dns` must
            # still follow the CALLER's proxy rather than that one.
            return effective, None, None

        async def _new_context(self, **_kw):
            context = AsyncMock()
            context.new_page = AsyncMock(return_value=AsyncMock())
            return context

    class _LoginRunner:
        async def replay(self, **kwargs):
            seen.update(kwargs)
            return SessionLoginResult(ok=True, took_ms=1), {}

    session = MagicMock()
    session.current_proxy = MagicMock(return_value=effective)
    monkeypatch.setattr(
        tasks_mod.proxy_resolver, "open_session", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(tasks_mod, "LoginRunner", _LoginRunner)
    monkeypatch.setattr(tasks_mod, "apply_page_masking", AsyncMock())

    out = await tasks_mod._run_login(
        _Runner(),
        {
            "session_pin": {"device": "desktop", "proxy_type": "none"},
            "script": {"steps": []},
            "creds": {},
        },
    )
    assert out["ok"] is True, out
    assert seen.get("resolve_dns") is expected_resolve_dns
