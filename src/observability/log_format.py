"""How a record is rendered — in two shapes, with one safety rule.

THE RULE: both renderers emit an explicit ALLOWLIST of fields. Neither ever
serialises `record.__dict__`, however convenient that is.

The reason is specific, not hygienic. `f1ee31e` merged a fix for credentials
reaching log lines, defended by `tests/proxy/test_no_credentials_in_logs.py` —
which asserts on `caplog.text`, rendered by PYTEST's formatter, not by the root
handler's. Measured: those tests cannot see this module at all. A dict-dumping
formatter would ship every attribute anything ever attaches to a record — a
proxy username (which on this provider encodes the session and geo targeting
and is the half worth stealing), a full URL with its query, a page body —
straight past `redact_url`, and all four tests would stay green.

The shipped text formatter has that property today by accident of naming five
fields. Here it is deliberate, and `test_an_undeclared_record_attribute_is_
never_emitted` is what keeps it.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from src.observability.log_context import CONTEXT_FIELDS

# Everything a rendered line may contain. Adding a name here is a decision
# about what may reach a log; there is no path that bypasses it.
_BASE_FIELDS = ("event", "url_host")
_EMITTABLE = (*_BASE_FIELDS, *CONTEXT_FIELDS)

# A traceback is kept as one escaped string rather than a nested structure: it
# stays greppable, and it cannot turn one incident into a thousand-line record.
#
# HEAD AND TAIL, not a prefix. A Python traceback puts the ACTIVE exception
# LAST, so `text[:N]` bounded the field and threw away the line that says what
# went wrong — on exactly the long Playwright and chained exceptions where the
# bound matters. The head keeps where it started, the tail keeps what failed.
_MAX_EXCEPTION_CHARS = 4000
_EXCEPTION_TAIL_CHARS = 1200
_TRUNCATION_MARK = "\n... [middle truncated] ...\n"


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _EMITTABLE:
        value = getattr(record, name, None)
        if value is None:
            continue
        try:
            # Rendered here rather than trusted to json/str later, so one
            # hostile value cannot cost the whole line.
            out[name] = value if isinstance(value, (str, int, float, bool)) else str(value)
        except Exception:  # pylint: disable=broad-except
            out[name] = "<unrenderable>"
    return out


def _message(record: logging.LogRecord) -> str:
    try:
        return record.getMessage()
    except Exception as exc:  # pylint: disable=broad-except
        # A bad printf pairing must not delete the line; the shipped formatter
        # would raise here and the record would never be printed.
        return f"<unformattable message: {type(exc).__name__}>"


def _exception(record: logging.LogRecord, fmt: logging.Formatter) -> str | None:
    if not record.exc_info:
        return None
    try:
        text = fmt.formatException(record.exc_info)
    except Exception:  # pylint: disable=broad-except
        return "<unformattable traceback>"
    if len(text) <= _MAX_EXCEPTION_CHARS:
        return text
    head = _MAX_EXCEPTION_CHARS - _EXCEPTION_TAIL_CHARS
    return text[:head] + _TRUNCATION_MARK + text[-_EXCEPTION_TAIL_CHARS:]


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Never a dict dump — see the module docstring."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "tag": getattr(record, "tag", None) or "?",
            "message": _message(record),
            **_extras(record),
        }
        exception = _exception(record, self)
        if exception is not None:
            payload["exception"] = exception
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:  # pylint: disable=broad-except
            return json.dumps(
                {"level": record.levelname, "logger": record.name,
                 "message": payload["message"], "log_render_error": True}
            )


class TextFormatter(logging.Formatter):
    """The shipped human shape, plus the correlation appended.

    Correlation is NOT held hostage to the format flag: nothing on this host
    can read JSON today, so text is what an operator actually greps, and
    `grep scrape.fetch_failed` has to work there.

    `tag` is defaulted rather than interpolated. The shipped formatter used
    `%(tag)s`, so a record arriving without one raised `ValueError: Formatting
    field not found in record: 'tag'` and the line was LOST outright — and
    three of the four long-lived processes can emit such a record (the
    scheduler never configures logging at all, and the taskiq worker master
    logs before the child hook runs).
    """

    _FORMAT = "%(asctime)s | %(levelname)s | [%(tag)s] | %(name)s | %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        extras = _extras(record)
        base = (
            f"{self.formatTime(record)} | {record.levelname} | "
            f"[{getattr(record, 'tag', None) or '?'}] | {record.name} | {_message(record)}"
        )
        if extras:
            base += " | " + " ".join(f"{k}={v}" for k, v in extras.items())
        exception = _exception(record, self)
        if exception is not None:
            base += "\n" + exception
        return base
