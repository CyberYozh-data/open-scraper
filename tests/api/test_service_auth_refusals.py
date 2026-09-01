"""A refusal has to leave a trace on THIS side of the wire.

Gating a surface risks exactly one thing: a caller that can no longer reach it.
And every caller here SWALLOWS the failure — the law-checker turns a 401 into
one warning and a proxy-less scan, the admin passthrough into an empty panel.
Without a line on the scraper's side there is nothing to correlate against,
and the only trace was a uvicorn.access INFO.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.app import create_app
from src.settings import settings


class _Capture(logging.Handler):
    """Records straight off the auth logger.

    `caplog` cannot be used here: `create_app()` runs `setup_logging`, which
    REMOVES every root handler — including the one pytest attached — so
    anything logged after the app is built is invisible to it. That is also why
    this log line had no test until now.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def auth_log():
    logger = logging.getLogger("src.api.service_auth")
    handler = _Capture()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


@pytest.mark.parametrize(
    "path",
    ["/api/v1/proxies/available?proxy_type=res_static", "/api/v2/prem-proxies/subscription"],
)
def test_a_missing_token_is_logged_with_the_path(path, monkeypatch, auth_log):
    monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
    with TestClient(create_app()) as client:
        assert client.get(path).status_code == 401
    assert any("service auth refused" in m for m in auth_log), auth_log
    assert any(path.split("?")[0] in m for m in auth_log), auth_log


def test_an_unconfigured_token_is_logged_as_a_503(monkeypatch, auth_log):
    """The fail-closed branch is the one an operator will hit by forgetting an
    env var, and for prem-proxies that 503 silently strips geo from every
    scan — so it must be the loudest thing this module does."""
    monkeypatch.setattr(settings, "service_token", None)
    with TestClient(create_app()) as client:
        assert client.get("/api/v2/prem-proxies/subscription").status_code == 503
    assert any("503" in m and "SERVICE_TOKEN" in m for m in auth_log), auth_log


def test_the_token_value_is_never_logged(monkeypatch, auth_log):
    """Not the configured secret, and not a hostile caller's guess at it."""
    monkeypatch.setattr(settings, "service_token", SecretStr("s3cret-do-not-log"))
    with TestClient(create_app()) as client:
        client.get(
            "/api/v2/prem-proxies/subscription",
            headers={"X-Service-Token": "GUESS-do-not-log"},
        )
    assert auth_log, "the refusal was not logged at all"
    for message in auth_log:
        assert "s3cret-do-not-log" not in message
        assert "GUESS-do-not-log" not in message


def test_a_valid_token_logs_nothing(monkeypatch, auth_log):
    """The hot path is every legitimate call; it must stay quiet."""
    monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
    with TestClient(create_app()) as client:
        client.get(
            "/api/v2/prem-proxies/subscription", headers={"X-Service-Token": "s3cret"}
        )
    assert auth_log == []


class TestTheProxyCatalogIsGatedToo:
    """HIGH-03 from the 2026-07 audit, still open: `/available` enumerates the
    PURCHASED proxies on the account — ids, hosts, ports, access type."""

    def test_available_refuses_an_anonymous_caller(self, monkeypatch):
        monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
        with TestClient(create_app()) as client:
            assert client.get(
                "/api/v1/proxies/available?proxy_type=res_static"
            ).status_code == 401

    def test_countries_stays_open_on_purpose(self, monkeypatch):
        """A static country list with nothing account-specific in it. Gating it
        would buy no secrecy while breaking a caller that needs no secret."""
        monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
        with TestClient(create_app()) as client:
            assert client.get("/api/v1/proxies/countries").status_code == 200


class TestTheLoggedPathCannotForgeARecord:
    """The path is caller-controlled and percent-decoded by the time it reaches
    `request.url.path`. The root formatter is plain text, so a separator in
    `/api/v1/sessions/{id}` forges a record — the same defect I fixed on the
    yozh side an hour earlier and did not carry here. Found by codex."""

    @pytest.mark.parametrize(
        "evil",
        ["x%0A2026-01-01 [ERROR] forged", "y%0D%0Aforged", "z%1B[31mred"],
    )
    def test_a_control_character_in_the_path_never_reaches_the_record(
        self, evil, monkeypatch, auth_log
    ):
        monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
        with TestClient(create_app()) as client:
            client.get(f"/api/v1/sessions/{evil}")
        assert auth_log, "the refusal was not logged at all"
        for message in auth_log:
            assert "\n" not in message and "\r" not in message, repr(message)
            assert "\x1b" not in message, repr(message)

    def test_an_enormous_path_does_not_flood_the_record(self, monkeypatch, auth_log):
        monkeypatch.setattr(settings, "service_token", SecretStr("s3cret"))
        with TestClient(create_app()) as client:
            client.get("/api/v1/sessions/" + "A" * 5000)
        assert auth_log
        for message in auth_log:
            assert len(message) < 400, len(message)
