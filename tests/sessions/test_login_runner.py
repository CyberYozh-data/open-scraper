from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.browser.login_runner import LoginRunner, _substitute_creds
from src.sessions.models import LoginScript, LoginStep



class TestSubstituteCreds:
    def test_replaces_creds_var(self):
        assert _substitute_creds("$creds_email", {"email": "a@b.com"}) == "a@b.com"

    def test_partial_match(self):
        assert (
            _substitute_creds("user=$creds_email&t=$creds_totp", {"email": "x", "totp": "1"})
            == "user=x&t=1"
        )

    def test_no_substitution(self):
        assert _substitute_creds("plain", {}) == "plain"

    def test_missing_var_leaves_token(self):
        assert _substitute_creds("$creds_missing", {}) == "$creds_missing"


class TestLoginRunnerVerbs:
    @pytest.fixture
    def page(self):
        page = MagicMock()
        page.goto = AsyncMock(return_value=None)
        page.fill = AsyncMock(return_value=None)
        page.click = AsyncMock(return_value=None)
        page.wait_for_selector = AsyncMock(return_value=None)
        page.wait_for_timeout = AsyncMock(return_value=None)
        page.press = AsyncMock(return_value=None)
        page.type = AsyncMock(return_value=None)
        page.hover = AsyncMock(return_value=None)
        page.screenshot = AsyncMock(return_value=b"PNG")
        page.url = "https://x.com/dashboard"
        return page

    @pytest.mark.asyncio
    async def test_goto_uses_url_field(self, page):
        runner = LoginRunner()
        await runner._execute_steps(
            page,
            LoginScript(steps=[LoginStep(op="goto", url="https://x.com/login")]),
            creds={},
        )
        page.goto.assert_awaited_once_with("https://x.com/login")

    @pytest.mark.asyncio
    async def test_fill_substitutes_creds(self, page):
        runner = LoginRunner()
        await runner._execute_steps(
            page,
            LoginScript(steps=[
                LoginStep(op="fill", selector="#email", value="$creds_email"),
            ]),
            creds={"email": "tom@x.com"},
        )
        page.fill.assert_awaited_once_with("#email", "tom@x.com")

    @pytest.mark.asyncio
    async def test_click_uses_selector(self, page):
        runner = LoginRunner()
        await runner._execute_steps(
            page,
            LoginScript(steps=[LoginStep(op="click", selector="#submit")]),
            creds={},
        )
        page.click.assert_awaited_once_with("#submit")

    @pytest.mark.asyncio
    async def test_press_key(self, page):
        runner = LoginRunner()
        await runner._execute_steps(
            page,
            LoginScript(steps=[LoginStep(op="press_key", selector="#u", key="Enter")]),
            creds={},
        )
        page.press.assert_awaited_once_with("#u", "Enter")

    @pytest.mark.asyncio
    async def test_wait_for_timeout(self, page):
        runner = LoginRunner()
        await runner._execute_steps(
            page,
            LoginScript(steps=[LoginStep(op="wait_for_timeout", ms=200)]),
            creds={},
        )
        page.wait_for_timeout.assert_awaited_once_with(200)

    @pytest.mark.asyncio
    async def test_wait_for_selector_uses_timeout_ms(self, page):
        runner = LoginRunner()
        await runner._execute_steps(
            page,
            LoginScript(steps=[
                LoginStep(op="wait_for_selector", selector="#x", timeout_ms=5_000),
            ]),
            creds={},
        )
        page.wait_for_selector.assert_awaited_once_with("#x", timeout=5_000)

    @pytest.mark.asyncio
    async def test_type_text_substitutes_creds(self, page):
        runner = LoginRunner()
        await runner._execute_steps(
            page,
            LoginScript(steps=[
                LoginStep(op="type_text", selector="#pw", value="$creds_password"),
            ]),
            creds={"password": "hunter2"},
        )
        page.type.assert_awaited_once_with("#pw", "hunter2")

    @pytest.mark.asyncio
    async def test_hover(self, page):
        runner = LoginRunner()
        await runner._execute_steps(
            page,
            LoginScript(steps=[LoginStep(op="hover", selector="#menu")]),
            creds={},
        )
        page.hover.assert_awaited_once_with("#menu")


class TestLoginRunnerResult:
    @pytest.fixture
    def page(self):
        page = MagicMock()
        page.goto = AsyncMock(
            return_value=MagicMock(request=MagicMock(redirected_from=None))
        )
        page.fill = AsyncMock()
        page.click = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.screenshot = AsyncMock(return_value=b"PNG")
        page.url = "https://x.com/dashboard"
        return page

    @pytest.fixture
    def context(self):
        browser_context = MagicMock()
        browser_context.storage_state = AsyncMock(return_value={"cookies": [], "origins": []})
        return browser_context

    @pytest.mark.asyncio
    async def test_ok_when_all_steps_pass(self, page, context):
        runner = LoginRunner()
        result, state = await runner.replay(
            page=page,
            context=context,
            script=LoginScript(steps=[
                LoginStep(op="goto", url="https://x.com/login"),
                LoginStep(op="fill", selector="#u", value="x"),
            ]),
            creds={},
        )
        assert result.ok is True
        assert result.failed_step_index is None
        assert result.screenshot_b64 is None
        assert state == {"cookies": [], "origins": []}

    @pytest.mark.asyncio
    async def test_failed_step_returns_screenshot(self, page, context):
        page.click.side_effect = RuntimeError("selector not found")
        runner = LoginRunner()
        result, state = await runner.replay(
            page=page,
            context=context,
            script=LoginScript(steps=[
                LoginStep(op="goto", url="https://x.com/login"),
                LoginStep(op="click", selector="#missing"),
            ]),
            creds={},
        )
        assert result.ok is False
        assert result.failed_step_index == 1
        assert result.failed_step is not None
        assert result.failed_step.op == "click"
        assert result.screenshot_b64 is not None
        assert "selector not found" in (result.error or "")
        assert state is None  # storage_state captured only on success

    @pytest.mark.asyncio
    async def test_failed_step_does_not_leak_credential_in_failed_step_value(
        self, page, context,
    ):
        page.fill.side_effect = RuntimeError("boom")
        runner = LoginRunner()
        result, state = await runner.replay(
            page=page,
            context=context,
            script=LoginScript(steps=[
                LoginStep(op="fill", selector="#pw", value="$creds_password"),
            ]),
            creds={"password": "hunter2"},
        )
        assert result.ok is False
        assert result.failed_step is not None
        assert result.failed_step.value == "$creds_password"
        assert state is None

    @pytest.mark.asyncio
    async def test_malformed_success_url_regex_returns_envelope(self, page, context):
        runner = LoginRunner()
        result, state = await runner.replay(
            page=page,
            context=context,
            script=LoginScript(
                steps=[LoginStep(op="goto", url="https://x.com/dashboard")],
                success_url_regex="[unclosed",
            ),
            creds={},
        )
        assert result.ok is False
        assert "invalid" in (result.error or "").lower()
        assert state is None

    @pytest.mark.asyncio
    async def test_credential_value_redacted_from_error(self, page, context):
        """Playwright errors can echo the substituted value; the response must not leak it."""
        page.fill.side_effect = RuntimeError("fill failed for value hunter2 on selector #pw")
        runner = LoginRunner()
        result, _ = await runner.replay(
            page=page,
            context=context,
            script=LoginScript(steps=[
                LoginStep(op="fill", selector="#pw", value="$creds_password"),
            ]),
            creds={"password": "hunter2"},
        )
        assert result.ok is False
        assert "hunter2" not in (result.error or "")
        assert "***" in (result.error or "")
