"""Three-tier parsing: deterministic -> self-heal -> AI-only.

1. Run the preset's CSS/XPath instructions. If every required field is
   populated, done (cheap path, no LLM).
2. If required fields are missing and self_heal + an LLM model are
   available, ask the model to regenerate selectors from the live HTML and
   re-extract. On success the healed instructions are returned so the caller
   can persist them.
3. If there are no instructions at all (AI-only preset) and an LLM model +
   schema/prompt are available, extract directly with the model.

This module does no I/O: persistence of healed instructions is the caller's
job (the worker), which keeps the pipeline unit-testable without a store.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from src.extract.extractor import extract_fields
from src.presets.llm.client import LLMError
from src.presets.llm.extract import llm_extract
from src.presets.llm.selector_gen import generate_selectors
from src.presets.models import ParsingInstructions

log = logging.getLogger(__name__)

Mode = Literal["deterministic", "self_healed", "llm_extracted", "none"]


@dataclass
class ParserResult:
    data: dict[str, Any] | None
    warnings: list[str] = field(default_factory=list)
    mode: Mode = "none"
    healed_instructions: ParsingInstructions | None = None


def _missing_required(
    instructions: ParsingInstructions, data: dict[str, Any]
) -> list[str]:
    return [
        name
        for name, rule in instructions.fields.items()
        if rule.required and not data.get(name)
    ]


def _schema_from(instructions: ParsingInstructions) -> dict[str, str]:
    # Coarse schema hint for the selector regenerator: just the field names.
    return {name: "string" for name in instructions.fields}


async def run(  # pylint: disable=too-many-return-statements
    page_html: str,
    instructions: ParsingInstructions | None,
    *,
    self_heal: bool,
    llm_model: str | None,
    output_schema: dict[str, Any] | None,
    llm_extract_prompt: str | None,
) -> ParserResult:
    if instructions is not None:
        data, warnings = extract_fields(page_html, instructions)
        if not _missing_required(instructions, data):
            return ParserResult(data=data, warnings=warnings, mode="deterministic")

        if self_heal and llm_model:
            try:
                healed = await generate_selectors(
                    page_html,
                    output_schema or _schema_from(instructions),
                    llm_model,
                )
            except LLMError as exc:
                log.warning("self-heal generation failed: %s", exc)
                return ParserResult(
                    data=data,
                    warnings=[*warnings, f"self_heal_failed: {exc}"],
                    mode="deterministic",
                )
            healed_data, healed_warnings = extract_fields(page_html, healed)
            if not _missing_required(healed, healed_data):
                return ParserResult(
                    data=healed_data,
                    warnings=[*warnings, *healed_warnings, "self_healed"],
                    mode="self_healed",
                    healed_instructions=healed,
                )
            warnings = [*warnings, "self_heal_did_not_recover"]

        # Deterministic parser ran (some required fields may be empty). Only
        # fall through to AI-only if it's actually configured; otherwise this
        # is still a deterministic result, just an incomplete one.
        if not (llm_model and (output_schema or llm_extract_prompt)):
            return ParserResult(
                data=data, warnings=warnings, mode="deterministic"
            )

    if llm_model and (output_schema or llm_extract_prompt):
        try:
            extracted = await llm_extract(
                page_html, output_schema, llm_extract_prompt, llm_model
            )
        except LLMError as exc:
            log.warning("AI-only extraction failed: %s", exc)
            return ParserResult(
                data=None,
                warnings=[f"llm_extract_failed: {exc}"],
                mode="none",
            )
        return ParserResult(
            data=extracted, warnings=["llm_extracted"], mode="llm_extracted"
        )

    return ParserResult(data=None, warnings=["no_parser"], mode="none")
