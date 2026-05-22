"""Extract a JSON value from an LLM response.

Models routinely wrap JSON in ```json fences or surround it with prose that
itself contains braces ("set the {price} field, here: {...}"). This scans
every `{`/`[` start, takes the first balanced span that actually parses, and
only fails when no candidate yields valid JSON — so a brace in the preamble
doesn't shadow the real payload that follows.
"""
from __future__ import annotations

import json
from typing import Any

from src.presets.llm.client import LLMError


def parse_json(text: str) -> Any:
    stripped = text.strip()
    # Fast path: already clean JSON.
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    last_error: str | None = None
    for start in _json_starts(stripped):
        span = _balanced_span(stripped, start)
        if span is None:
            continue
        try:
            return json.loads(span)
        except json.JSONDecodeError as exc:
            last_error = str(exc)
            continue

    if last_error is not None:
        raise LLMError(f"no parseable JSON in LLM response: {last_error}")
    raise LLMError(f"no JSON found in LLM response: {text[:200]!r}")


def _json_starts(text: str):
    for idx, char in enumerate(text):
        if char in "{[":
            yield idx


def _balanced_span(text: str, start: int) -> str | None:
    """Return the substring from `start` to its matching closer, or None if
    the span never balances (truncated / unbalanced)."""
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None
