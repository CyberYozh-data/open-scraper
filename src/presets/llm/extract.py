"""Direct LLM extraction (no selectors).

Used by AI-only presets and as the final fallback in the parser pipeline:
the model reads the page and returns the structured data directly, guided by
either a JSON schema, a natural-language instruction, or both.
"""
from __future__ import annotations

from typing import Any

from src.presets.llm.client import LLMError, complete
from src.presets.llm.htmlclean import clean_html
from src.presets.llm.jsonparse import parse_json

_SYSTEM = (
    "You extract structured data from web pages. Return ONLY a JSON object "
    "with the requested fields. If a value is absent, use null. No prose, "
    "no code fences."
)


async def llm_extract(
    page_html: str,
    schema: dict[str, Any] | None,
    prompt: str | None,
    model: str,
    *,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    if schema is None and prompt is None:
        raise LLMError("llm_extract requires a schema or a prompt")

    instruction = []
    if schema is not None:
        instruction.append(f"Target schema:\n{schema}")
    if prompt is not None:
        instruction.append(f"Instruction: {prompt}")
    instruction.append(f"HTML:\n{clean_html(page_html)}")

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": "\n\n".join(instruction)},
    ]
    raw = await complete(model, messages, max_tokens=max_tokens)
    obj = parse_json(raw)
    if not isinstance(obj, dict):
        raise LLMError(
            f"LLM extraction must return a JSON object, got {type(obj).__name__}"
        )
    return obj
