"""Cleanup failures must be visible.

`_teardown` runs on EVERY scrape and closed page, context and SOCKS bridge
under `contextlib.suppress(Exception)` — no log line anywhere. A page that
will not close is a leaked browser context, and on a worker that runs one task
per process a slow leak ends as an OOM kill with nothing in the logs pointing
at the cause. Suppressing is right (a teardown fault must not fail a fetch that
already succeeded); suppressing SILENTLY is not.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.browser.runner import PlaywrightRunner


def _runner() -> PlaywrightRunner:
    return PlaywrightRunner(headless=True, block_assets=False, timeout_ms=1000)


async def _fetch_with(runner, *, page, context, bridge_cm=None):
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    runner._browser = browser
    runner.start = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    with patch("src.browser.runner.apply_page_masking", AsyncMock()), \
            patch.object(runner, "resolve_proxy", AsyncMock(return_value=(None, bridge_cm, None))):
        return await runner.fetch(
            url="https://example.com/p", device="desktop", proxy=None, headers=None,
            wait_until="domcontentloaded", wait_for_selector=None, timeout_ms=1000,
            screenshot=False,
        )


def _good_page():
    page = AsyncMock()
    page.url = "https://example.com/p"
    page.goto = AsyncMock(
        return_value=MagicMock(status=200, request=MagicMock(redirected_from=None))
    )
    page.content = AsyncMock(return_value="<html>ok</html>")
    return page


@pytest.mark.asyncio
async def test_a_page_that_will_not_close_is_logged(caplog):
    runner = _runner()
    page = _good_page()
    page.close = AsyncMock(side_effect=RuntimeError("target closed"))
    with caplog.at_level(logging.DEBUG):
        result = await _fetch_with(runner, page=page, context=AsyncMock())

    # Still non-fatal: a teardown fault must not fail a fetch that succeeded.
    assert result.ok is True
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("page" in m.lower() for m in messages), messages


@pytest.mark.asyncio
async def test_a_context_that_will_not_close_is_logged(caplog):
    runner = _runner()
    context = AsyncMock()
    context.close = AsyncMock(side_effect=RuntimeError("browser gone"))
    with caplog.at_level(logging.DEBUG):
        result = await _fetch_with(runner, page=_good_page(), context=context)

    assert result.ok is True
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("context" in m.lower() for m in messages), messages


@pytest.mark.asyncio
async def test_a_bridge_that_will_not_close_is_logged(caplog):
    """The SOCKS bridge is a listening socket. One leaked per scrape is a file
    descriptor leak, and it is the least visible of the three."""
    runner = _runner()
    bridge = AsyncMock()
    bridge.__aexit__ = AsyncMock(side_effect=RuntimeError("bridge stuck"))
    with caplog.at_level(logging.DEBUG):
        result = await _fetch_with(
            runner, page=_good_page(), context=AsyncMock(), bridge_cm=bridge
        )

    assert result.ok is True
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("bridge" in m.lower() for m in messages), messages


@pytest.mark.asyncio
async def test_a_clean_teardown_closes_everything_and_logs_nothing(caplog):
    """The hot path is every successful scrape: it must stay silent AND must
    actually close. Asserting only the silence passed with `_teardown` gutted
    to a no-op."""
    runner = _runner()
    page = _good_page()
    context = AsyncMock()
    bridge = AsyncMock()
    with caplog.at_level(logging.DEBUG):
        result = await _fetch_with(runner, page=page, context=context, bridge_cm=bridge)

    assert result.ok is True
    page.close.assert_awaited_once()
    context.close.assert_awaited_once()
    bridge.__aexit__.assert_awaited_once()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_a_failure_does_not_stop_the_remaining_closes(caplog):
    """The invariant the restructure had to preserve, and the one a `break`
    would silently destroy: three independent suppressions, not a chain."""
    runner = _runner()
    page = _good_page()
    page.close = AsyncMock(side_effect=RuntimeError("target closed"))
    context = AsyncMock()
    bridge = AsyncMock()
    with caplog.at_level(logging.DEBUG):
        await _fetch_with(runner, page=page, context=context, bridge_cm=bridge)

    context.close.assert_awaited_once()
    bridge.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_close_that_hangs_is_bounded_and_logged(monkeypatch, caplog):
    """A browser that is wedged but ALIVE does not raise — it never returns.
    `_teardown` runs in a `finally`, so without a deadline the fetch hangs and
    nothing is logged at all. This is the failure the log line is actually for.
    """
    import src.browser.runner as runner_mod

    monkeypatch.setattr(runner_mod, "_TEARDOWN_TIMEOUT_S", 0.05)
    runner = _runner()
    page = _good_page()

    async def _never_returns():
        await asyncio.sleep(3600)

    page.close = AsyncMock(side_effect=_never_returns)
    context = AsyncMock()
    with caplog.at_level(logging.DEBUG):
        # Bounded from the outside too: without the production deadline this
        # would hang forever, and a hung test is a CI timeout rather than a
        # readable failure. `pytest.fail` names what actually broke.
        try:
            result = await asyncio.wait_for(
                _fetch_with(runner, page=page, context=context), timeout=10
            )
        except asyncio.TimeoutError:
            pytest.fail("teardown has no deadline: a wedged close hangs the fetch")

    assert result.ok is True
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("page" in m.lower() for m in messages), messages
    # The others still run after the deadline fires.
    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_warning_names_the_scrape_it_belongs_to(caplog):
    """`browser teardown: page did not close` gives a count, not a culprit.
    An operator chasing a leak needs to know WHICH scrape."""
    runner = _runner()
    page = _good_page()
    page.close = AsyncMock(side_effect=RuntimeError("target closed"))
    with caplog.at_level(logging.DEBUG):
        await _fetch_with(runner, page=page, context=AsyncMock())

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("example.com" in m for m in messages), messages


class TestTheLoginTaskTearsDownTheSameWay:
    """The login task has its own copy of the same three closes, and it was
    missed.

    The original report named `tasks.py:454-465` alongside two lines in
    `runner.py`. The runner pair turned out to be misclassified — a narrow
    `except PWError` around a networkidle wait, not cleanup — and in showing
    that, the one the report had RIGHT was dropped. Raimonte caught it on
    review: three `except Exception: pass`, no log, no deadline, on the path
    that holds an authenticated browser session.
    """

    @staticmethod
    async def _run_login(monkeypatch, *, page, context, bridge=None):
        import src.queue.tasks as tasks_mod
        from src.proxy.models import ProxyConfig
        from src.sessions.models import SessionLoginResult

        class _Runner:
            _engine = "chromium"
            start = AsyncMock()

            async def resolve_proxy(self, _proxy):
                return ProxyConfig(server="http://p.example:8080"), bridge, None

            async def _new_context(self, **_kw):
                return context

        class _LoginRunner:
            async def replay(self, **_kw):
                return SessionLoginResult(ok=True, took_ms=1), {}

        session = MagicMock()
        session.current_proxy = MagicMock(return_value=None)
        monkeypatch.setattr(
            tasks_mod.proxy_resolver, "open_session", AsyncMock(return_value=session)
        )
        monkeypatch.setattr(tasks_mod, "LoginRunner", _LoginRunner)
        monkeypatch.setattr(tasks_mod, "apply_page_masking", AsyncMock())
        context.new_page = AsyncMock(return_value=page)

        return await tasks_mod._run_login(
            _Runner(),
            {
                "session_pin": {"device": "desktop", "proxy_type": "none"},
                "script": {"steps": []},
                "creds": {},
            },
            login_id="req_login123",
        )

    @pytest.mark.asyncio
    async def test_a_page_that_will_not_close_is_logged(self, monkeypatch, caplog):
        page = AsyncMock()
        page.close = AsyncMock(side_effect=RuntimeError("target closed"))
        with caplog.at_level(logging.DEBUG):
            out = await self._run_login(monkeypatch, page=page, context=AsyncMock())

        assert out["ok"] is True
        messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("page" in m.lower() for m in messages), messages

    @pytest.mark.asyncio
    async def test_a_failure_does_not_stop_the_remaining_closes(self, monkeypatch, caplog):
        page = AsyncMock()
        page.close = AsyncMock(side_effect=RuntimeError("target closed"))
        context = AsyncMock()
        bridge = AsyncMock()
        with caplog.at_level(logging.DEBUG):
            await self._run_login(monkeypatch, page=page, context=context, bridge=bridge)

        context.close.assert_awaited_once()
        bridge.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_close_that_hangs_is_bounded(self, monkeypatch, caplog):
        import src.queue.tasks as tasks_mod

        monkeypatch.setattr(tasks_mod, "_TEARDOWN_TIMEOUT_S", 0.05)
        page = AsyncMock()

        async def _never_returns():
            await asyncio.sleep(3600)

        page.close = AsyncMock(side_effect=_never_returns)
        context = AsyncMock()
        with caplog.at_level(logging.DEBUG):
            try:
                out = await asyncio.wait_for(
                    self._run_login(monkeypatch, page=page, context=context), timeout=10
                )
            except asyncio.TimeoutError:
                pytest.fail("the login teardown has no deadline: a wedged close hangs it")

        assert out["ok"] is True
        context.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_warning_names_a_real_identifier(self, monkeypatch, caplog):
        """The first version read `session_pin["session_id"]`, which the
        production payload never contains — `SessionService.login` builds that
        dict from device, viewport and proxy fields only. So every warning
        would have read `for ?` and the correlation this line exists for was
        dead on arrival. Found by the codex second pass.
        """
        page = AsyncMock()
        page.close = AsyncMock(side_effect=RuntimeError("target closed"))
        with caplog.at_level(logging.DEBUG):
            await self._run_login(monkeypatch, page=page, context=AsyncMock())

        messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("req_login123" in m for m in messages), messages
        assert not any(m.endswith("for ?") or " for ? " in m for m in messages), messages

    @pytest.mark.asyncio
    async def test_a_clean_login_teardown_is_silent(self, monkeypatch, caplog):
        page = AsyncMock()
        context = AsyncMock()
        with caplog.at_level(logging.DEBUG):
            await self._run_login(monkeypatch, page=page, context=context)

        page.close.assert_awaited_once()
        context.close.assert_awaited_once()
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
