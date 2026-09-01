"""Wiring. An unwired formatter is a formatter nobody sees."""
from __future__ import annotations

import json
import logging

import pytest

from src.observability.log_context import bind_log_context
from src.observability.log_format import JsonFormatter, TextFormatter
from src.settings import Settings, setup_logging


@pytest.fixture(autouse=True)
def _restore():
    yield
    logging.setLogRecordFactory(logging.LogRecord)
    for h in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(h)


def _formatter() -> logging.Formatter:
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1, f"expected one root handler, got {handlers}"
    return handlers[0].formatter


def test_text_is_the_default_shape(monkeypatch):
    """Nothing on this host can read JSON — the driver is json-file with no
    shipper — and two of the four long-lived processes could not emit it
    anyway. Defaulting to JSON would advertise a guarantee the deployment
    cannot keep."""
    assert Settings.model_fields["log_format"].default == "text"


def test_setup_installs_the_text_renderer_by_default(monkeypatch):
    monkeypatch.setattr("src.settings.settings.log_format", "text")
    setup_logging("INFO", tag="T")
    assert isinstance(_formatter(), TextFormatter)


def test_setup_installs_the_json_renderer_when_asked(monkeypatch):
    monkeypatch.setattr("src.settings.settings.log_format", "json")
    setup_logging("INFO", tag="T")
    assert isinstance(_formatter(), JsonFormatter)


def test_the_record_factory_is_installed_so_context_reaches_the_line(monkeypatch, capsys):
    monkeypatch.setattr("src.settings.settings.log_format", "json")
    setup_logging("INFO", tag="T")
    with bind_log_context(job_id="req_abc", request_id="req_run"):
        logging.getLogger("probe").info("hello")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["job_id"] == "req_abc"
    assert parsed["request_id"] == "req_run"
    assert parsed["tag"] == "T"


def test_calling_setup_twice_leaves_one_handler_and_one_factory(monkeypatch):
    """It runs once per process, but tests call it repeatedly — and a factory
    that wraps whatever it found would grow a chain and eventually recurse."""
    monkeypatch.setattr("src.settings.settings.log_format", "text")
    setup_logging("INFO", tag="T")
    first = logging.getLogRecordFactory()
    setup_logging("INFO", tag="T")
    assert logging.getLogRecordFactory() is first
    assert len(logging.getLogger().handlers) == 1


def test_the_tag_survives_in_both_shapes(monkeypatch, capsys):
    for fmt in ("text", "json"):
        monkeypatch.setattr("src.settings.settings.log_format", fmt)
        setup_logging("INFO", tag="W")
        logging.getLogger("probe").info("hello")
        err = capsys.readouterr().err
        assert "W" in err, fmt


def test_every_long_lived_process_configures_logging():
    """The scheduler never called `setup_logging`, so 100% of its container log
    was in taskiq's format — outside the contract the other services keep, and
    invisible to LOG_FORMAT. A JSON default would have promised `docker logs
    scheduler | jq` and failed on every line.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    entrypoints = {
        "src/app.py": "M",
        "src/queue/worker.py": "W",
        "src/queue/scheduler.py": "S",
    }
    for rel, tag in entrypoints.items():
        source = (root / rel).read_text()
        assert "setup_logging(" in source, f"{rel} never configures logging"
        assert f'tag="{tag}"' in source, f"{rel} should identify itself as {tag}"

    tags = [t for t in entrypoints.values()]
    assert len(set(tags)) == len(tags), "two processes would be indistinguishable"
