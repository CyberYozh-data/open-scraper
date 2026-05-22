"""Infer an output schema from a natural-language description.

Powers /presets/generate (from_prompt): the user says "grab the title and
price"; the model turns that + a sample page into a JSON schema, which is
then fed to selector_gen so the saved preset has a deterministic parser.
"""
from __future__ import annotations

from typing import Any

from src.presets.llm.client import LLMError, complete
from src.presets.llm.htmlclean import clean_html
from src.presets.llm.jsonparse import parse_json

_SYSTEM = """You design extraction schemas. Given a sample page and a \
plain-language description of what to extract, output ONLY a JSON object \
mapping field names to {"type": "string"|"number"|"integer"|"boolean"|\
"array"} (add a "description" where helpful). No prose, no code fences."""


async def infer_schema(
    page_html: str,
    description: str,
    model: str,
    *,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Extraction request: {description}\n\n"
                f"Sample HTML:\n{clean_html(page_html)}"
            ),
        },
    ]
    raw = await complete(model, messages, max_tokens=max_tokens)
    obj = parse_json(raw)
    if not isinstance(obj, dict):
        raise LLMError(
            f"inferred schema must be a JSON object, got {type(obj).__name__}"
        )
    return obj
