from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.sessions.models import (
    LoginScript,
    LoginStep,
    SessionCreateRequest,
    SessionLoginRequest,
    SessionRecord,
)


class TestLoginStep:
    def test_goto_step_accepts_url(self):
        step = LoginStep(op="goto", url="https://example.com")
        assert step.op == "goto"
        assert step.url == "https://example.com"

    def test_fill_step_requires_selector_and_value(self):
        step = LoginStep(op="fill", selector="#email", value="$creds_email")
        assert step.selector == "#email"

    def test_unknown_op_rejected(self):
        with pytest.raises(ValidationError):
            LoginStep(op="eval_js", value="alert(1)")  # type: ignore[arg-type]

    def test_press_key_step(self):
        step = LoginStep(op="press_key", selector="#email", key="Enter")
        assert step.key == "Enter"

    def test_wait_for_timeout_uses_ms_field(self):
        step = LoginStep(op="wait_for_timeout", ms=500)
        assert step.ms == 500


class TestLoginScript:
    def test_minimal_login_script(self):
        script = LoginScript(steps=[
            LoginStep(op="goto", url="https://x.com/login"),
            LoginStep(op="fill", selector="#u", value="$creds_email"),
        ])
        assert len(script.steps) == 2

    def test_success_selector_optional(self):
        script = LoginScript(steps=[], success_selector=".dashboard")
        assert script.success_selector == ".dashboard"

    def test_empty_steps_allowed_but_useless(self):
        script = LoginScript(steps=[])
        assert script.steps == []


class TestSessionCreateRequest:
    def test_defaults(self):
        req = SessionCreateRequest()
        assert req.device == "desktop"
        assert req.proxy_type == "none"
        assert req.ttl_seconds == 86400

    def test_ttl_clamped_low(self):
        with pytest.raises(ValidationError):
            SessionCreateRequest(ttl_seconds=10)

    def test_ttl_clamped_high(self):
        with pytest.raises(ValidationError):
            SessionCreateRequest(ttl_seconds=86400 * 30)


class TestSessionLoginRequest:
    def test_creds_required(self):
        with pytest.raises(ValidationError):
            SessionLoginRequest(script=LoginScript(steps=[]))  # type: ignore[call-arg]

    def test_creds_arbitrary_keys(self):
        req = SessionLoginRequest(
            script=LoginScript(steps=[]),
            creds={"email": "a@b.com", "password": "x", "totp_secret": "JBSWY"},
        )
        assert req.creds["totp_secret"] == "JBSWY"


class TestSessionRecord:
    def test_initial_state(self):
        rec = SessionRecord(
            session_id="sess_abc",
            status="created",
            created_at=100.0,
            expires_at=86500.0,
            last_used_at=100.0,
            device="desktop",
            proxy_type="none",
        )
        assert rec.status == "created"
        assert rec.storage_state is None
