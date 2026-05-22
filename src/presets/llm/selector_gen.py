"""LLM-generated CSS/XPath selectors.

Given a cleaned page and a desired field schema, ask the model for a
ParsingInstructions JSON (the same deterministic DSL the worker runs). Used
by /presets/generate (from_schema) and by self-heal when a built-in's
selectors stop matching.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from src.presets.llm.client import LLMError, complete
from src.presets.llm.htmlclean import clean_html
from src.presets.llm.jsonparse import parse_json
from src.presets.models import ParsingInstructions

_SYSTEM = (
    "You generate web-scraping selectors. Given an HTML excerpt and a target "
    "field schema, output ONLY a JSON object matching this shape: "
    '{"type":"css"|"xpath","fields":{"<name>":{"selector":"<expr>",'
    '"attr":"text"|"html"|"<attr>","all":bool,"required":bool,'
    '"post_process":[{"op":"...","args":[...]}]}}}. '
    "Prefer stable selectors (ids, data-* attributes) over positional ones. "
    "Use post_process ops (parse_price, parse_int, regex, strip) to coerce "
    "values. Output JSON only, no prose, no code fences."
)


async def generate_selectors(
    page_html: str,
    schema: dict[str, Any],
    model: str,
    *,
    max_tokens: int | None = None,
) -> ParsingInstructions:
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Target field schema:\n{schema}\n\n"
                f"HTML:\n{clean_html(page_html)}"
            ),
        },
    ]
    raw = await complete(model, messages, max_tokens=max_tokens)
    obj = parse_json(raw)
    try:
        return ParsingInstructions.model_validate(obj)
    except ValidationError as exc:
        raise LLMError(
            f"LLM did not return valid ParsingInstructions: {exc}"
        ) from exc
