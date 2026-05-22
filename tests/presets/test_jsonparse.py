from __future__ import annotations

import pytest

from src.presets.llm.client import LLMError
from src.presets.llm.jsonparse import parse_json


class TestHappyPath:
    def test_plain_object(self):
        assert parse_json('{"a": 1}') == {"a": 1}

    def test_plain_array(self):
        assert parse_json("[1, 2, 3]") == [1, 2, 3]

    def test_fenced_with_lang_tag(self):
        assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_brace_inside_string(self):
        assert parse_json('{"a": "}"}') == {"a": "}"}

    def test_escaped_backslash_then_close(self):
        assert parse_json('{"a": "\\\\"}') == {"a": "\\"}

    def test_nested(self):
        assert parse_json('[{"a": [1, {"b": 2}]}]') == [{"a": [1, {"b": 2}]}]


class TestProseBracesBeforeJson:
    def test_prose_with_curly_then_real_json(self):
        # The bug: a balanced {curly} span appears before the real JSON.
        text = 'use the {price} field, here it is: {"x": 1}'
        assert parse_json(text) == {"x": 1}

    def test_multiple_invalid_spans_then_valid(self):
        text = "first {nope} then {also bad} finally {\"ok\": true}"
        assert parse_json(text) == {"ok": True}

    def test_array_prose_then_object(self):
        text = "options [a, b] and the result: {\"done\": 1}"
        assert parse_json(text) == {"done": 1}

    def test_returns_first_valid_json(self):
        # Two valid objects — first one wins (documented behaviour).
        assert parse_json('noise {"a":1} more {"b":2}') == {"a": 1}


class TestFailures:
    def test_no_json_raises(self):
        with pytest.raises(LLMError):
            parse_json("absolutely no json here")

    def test_all_spans_invalid_raises(self):
        with pytest.raises(LLMError):
            parse_json("{not json} {still {unbalanced")
