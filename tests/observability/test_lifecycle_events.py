"""Stable event names on the lines that answer an operational question.

The point is not to rewrite 147 call sites. It is that "is this feature
working?" and "what happened to job X?" become one query — `grep
scrape.fetch.succeeded` in text mode, a field match in JSON — instead of
timestamp archaeology across free-text messages that no two of which phrase
the same thing the same way.
"""
from __future__ import annotations

import logging

import pytest

from src.observability.log_context import install_record_factory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _factory():
    install_record_factory()
    yield
    logging.setLogRecordFactory(logging.LogRecord)


def _events(caplog) -> list[str]:
    return [e for e in (getattr(r, "event", None) for r in caplog.records) if e]


async def test_a_successful_run_emits_the_lifecycle_events(monkeypatch, caplog):
    from src.queue import scrape_runner

    runner = _stub_runner(ok=True)
    with caplog.at_level(logging.DEBUG):
        await scrape_runner.run_scrape(
            runner, "req_x", {"url": "https://e.com", "proxy_type": "none"}, None
        )
    events = _events(caplog)
    assert "scrape.job.received" in events
    assert "scrape.attempt.started" in events
    assert "scrape.fetch.succeeded" in events


async def test_a_failing_run_names_the_failure(monkeypatch, caplog):
    from src.queue import scrape_runner

    runner = _stub_runner(ok=False)
    with caplog.at_level(logging.DEBUG):
        await scrape_runner.run_scrape(
            runner, "req_x", {"url": "https://e.com", "proxy_type": "none"}, None
        )
    events = _events(caplog)
    assert "scrape.job.received" in events
    assert "scrape.attempt.failed" in events


async def test_every_event_name_is_dotted_and_lowercase(caplog):
    """A name that drifts in shape is a name nobody can query for."""
    from src.queue import scrape_runner

    runner = _stub_runner(ok=True)
    with caplog.at_level(logging.DEBUG):
        await scrape_runner.run_scrape(
            runner, "req_x", {"url": "https://e.com", "proxy_type": "none"}, None
        )
    for event in _events(caplog):
        assert event == event.lower(), event
        assert "." in event, event
        assert " " not in event, event


async def test_no_event_line_carries_a_full_url(caplog):
    """A query string routinely carries an API key, a session token or PII,
    and a full-URL field is unbounded cardinality besides. Host only."""
    from src.queue import scrape_runner

    secret = "https://e.com/x?apikey=SUPERSECRET"
    runner = _stub_runner(ok=True)
    with caplog.at_level(logging.DEBUG):
        await scrape_runner.run_scrape(
            runner, "req_x", {"url": secret, "proxy_type": "none"}, None
        )
    for record in caplog.records:
        for name in ("url", "url_host", "target"):
            value = getattr(record, name, None)
            if value is not None:
                assert "SUPERSECRET" not in str(value), f"{name}={value!r}"


def _stub_runner(*, ok: bool):
    from unittest.mock import AsyncMock, MagicMock

    from src.browser.runner import FetchResult

    result = FetchResult(
        html="<html>ok</html>" if ok else "",
        final_url="https://e.com" if ok else None,
        status_code=200 if ok else None,
        screenshot_b64=None,
        ok=ok,
        error=None if ok else "boom",
    )
    runner = MagicMock()
    runner.fetch = AsyncMock(return_value=result)
    return runner


async def test_an_attempt_the_budget_refuses_is_not_counted_as_started(monkeypatch, caplog):
    """`scrape.attempt.started` was emitted BEFORE the budget gate, so an
    attempt the deadline refused was logged as started and then, one line
    later, as not started. Any query counting attempts overcounted. Found by
    the codex second pass."""
    from src.queue import scrape_runner

    runner = _stub_runner(ok=True)
    # A deadline already in the past: the budget refuses attempt 1 outright.
    with caplog.at_level(logging.DEBUG):
        await scrape_runner.run_scrape(
            runner, "req_x", {"url": "https://e.com", "proxy_type": "none"}, None,
            deadline=0.0,
        )

    events = _events(caplog)
    assert "scrape.attempt.started" not in events, events
    runner.fetch.assert_not_awaited()


async def test_an_attempt_that_does_run_is_still_counted(caplog):
    """The control: the event must not simply disappear."""
    from src.queue import scrape_runner

    runner = _stub_runner(ok=True)
    with caplog.at_level(logging.DEBUG):
        await scrape_runner.run_scrape(
            runner, "req_x", {"url": "https://e.com", "proxy_type": "none"}, None
        )
    assert "scrape.attempt.started" in _events(caplog)
    runner.fetch.assert_awaited()
