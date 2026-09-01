"""The JSON renderer, and the credential invariant it must not break.

PR #100 closed a credential leak in log lines. The four tests defending it
(`tests/proxy/test_no_credentials_in_logs.py`) assert on `caplog.text`, which
pytest renders with ITS OWN formatter — measured. So none of them can see this
renderer at all, and the idiomatic implementation, dumping `record.__dict__`
minus a reserved set, would emit every attribute anything ever attaches to a
record without passing it through `redact_url`. These tests are that missing
half.
"""
from __future__ import annotations

import json
import logging

import pytest

from src.observability.log_format import JsonFormatter, TextFormatter


def _record(msg="hello %s", args=("world",), **attrs) -> logging.LogRecord:
    rec = logging.getLogger("probe").makeRecord(
        "probe", logging.INFO, "f.py", 7, msg, args, None
    )
    for k, v in attrs.items():
        setattr(rec, k, v)
    return rec


class TestJsonRenderer:
    def test_emits_one_parseable_line(self):
        out = JsonFormatter().format(_record())
        assert "\n" not in out
        parsed = json.loads(out)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "probe"

    def test_carries_the_context_fields_when_present(self):
        out = json.loads(JsonFormatter().format(_record(job_id="req_abc", page_index=2)))
        assert out["job_id"] == "req_abc"
        assert out["page_index"] == 2

    def test_omits_context_fields_that_are_absent(self):
        out = json.loads(JsonFormatter().format(_record()))
        assert "job_id" not in out

    def test_an_undeclared_record_attribute_is_never_emitted(self):
        """THE credential test for this path.

        A formatter that serialises `record.__dict__` would ship anything an
        `extra=` or a record factory ever attached — proxy usernames, a full
        URL with its query, a page body — straight past `redact_url`. Today's
        text formatter renders five named fields and physically cannot leak an
        unlisted attribute; this one must have the same property.
        """
        leaked = _record(
            proxy_dict={"username": "USER", "password": "PASS"},
            raw_html="<html>secret</html>",
            url="https://x.example/?apikey=SECRET",
        )
        out = JsonFormatter().format(leaked)
        for needle in ("USER", "PASS", "secret", "SECRET", "apikey"):
            assert needle not in out, f"{needle!r} reached the log line"

    def test_the_event_name_is_a_first_class_field(self):
        out = json.loads(JsonFormatter().format(_record(event="scrape.attempt.started")))
        assert out["event"] == "scrape.attempt.started"

    def test_a_traceback_is_a_bounded_string_field(self):
        try:
            raise ValueError("kaboom")
        except ValueError:
            rec = logging.getLogger("probe").makeRecord(
                "probe", logging.ERROR, "f.py", 7, "died", (), __import__("sys").exc_info()
            )
        out = json.loads(JsonFormatter().format(rec))
        assert "kaboom" in out["exception"]
        assert "\n" not in JsonFormatter().format(rec)

    def test_an_unserialisable_value_does_not_lose_the_line(self):
        """A record that cannot be rendered must still produce a line — losing
        it silently is the failure mode the text formatter already has when a
        record arrives without a tag."""

        class _Hostile:
            def __repr__(self):
                raise RuntimeError("boom")

        out = JsonFormatter().format(_record(job_id=_Hostile()))
        assert json.loads(out)["message"] == "hello world"


class TestTextRenderer:
    def test_keeps_the_shipped_shape(self):
        out = TextFormatter().format(_record(tag="W"))
        assert " | INFO | [W] | probe | hello world" in out

    def test_a_record_without_a_tag_still_renders(self):
        """The shipped formatter raises `ValueError: Formatting field not found
        in record: 'tag'` and the line is LOST — measured. Three of the four
        long-lived processes can emit such a record."""
        out = TextFormatter().format(_record())
        assert "hello world" in out

    def test_appends_the_context_so_text_mode_is_correlated_too(self):
        """Correlation must not be hostage to the format flag: nothing can read
        JSON on this host today, so text is what an operator actually greps."""
        out = TextFormatter().format(_record(tag="W", job_id="req_abc", event="scrape.done"))
        assert "req_abc" in out
        assert "scrape.done" in out


class TestTruncationKeepsTheDecisiveEnd:
    """A Python traceback puts the ACTIVE exception last.

    Bounding the size with `text[:4000]` kept the head — the outermost frames —
    and threw away the line that says what actually went wrong. On a long
    Playwright or chained exception that is the whole diagnostic. Found by the
    codex second pass: I bounded the field and discarded its value.
    """

    @staticmethod
    def _long_traceback_record() -> logging.LogRecord:
        """A genuinely long one.

        Deep recursion does NOT work: Python collapses repeated frames into
        "[Previous line repeated N more times]", so the first version of this
        produced a short traceback and the tests passed without exercising the
        truncation at all — the same trap as asserting a property you cannot
        see. A long exception MESSAGE is what actually lands at the end.
        """
        import sys

        # The marker is ASSEMBLED, never written literally. A traceback echoes
        # the raising SOURCE LINE, so a literal marker appears near the head of
        # the text and `marker in exception` passes on that copy while the
        # exception VALUE at the end is gone — the first version of this test
        # asserted the property and could not see it.
        marker = "THE-DECISIVE" + "-LINE-AT-THE-END"
        try:
            raise ValueError("PAD " * 1500 + marker)
        except ValueError:
            return logging.getLogger("probe").makeRecord(
                "probe", logging.ERROR, "f.py", 1, "died", (), sys.exc_info()
            )

    MARKER = "THE-DECISIVE" + "-LINE-AT-THE-END"

    def test_the_active_exception_survives(self):
        out = json.loads(JsonFormatter().format(self._long_traceback_record()))
        assert self.MARKER in out["exception"]

    def test_the_bound_is_still_enforced(self):
        from src.observability.log_format import _MAX_EXCEPTION_CHARS

        out = json.loads(JsonFormatter().format(self._long_traceback_record()))
        assert len(out["exception"]) <= _MAX_EXCEPTION_CHARS + 80

    def test_the_head_survives_too(self):
        """Head AND tail: the outermost frame says where it started."""
        out = json.loads(JsonFormatter().format(self._long_traceback_record()))
        assert "Traceback" in out["exception"]

    def test_a_short_traceback_is_untouched(self):
        try:
            raise ValueError("small")
        except ValueError:
            import sys

            rec = logging.getLogger("probe").makeRecord(
                "probe", logging.ERROR, "f.py", 1, "died", (), sys.exc_info()
            )
        out = json.loads(JsonFormatter().format(rec))
        assert "truncated" not in out["exception"]
        assert "small" in out["exception"]


class TestTheHostFieldExists:
    """The code comment promised "the queryable field is the HOST only" and
    then attached nothing but `event`, so the documented capability did not
    exist and a consumer had to parse the full URL out of the message."""

    def test_a_host_field_is_emitted_when_present(self):
        out = json.loads(JsonFormatter().format(_record(url_host="example.com")))
        assert out["url_host"] == "example.com"

    def test_it_is_absent_when_not_set(self):
        assert "url_host" not in json.loads(JsonFormatter().format(_record()))
