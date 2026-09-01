"""Worker-side entry point for preset parsing.

The worker subprocess calls `apply()` after rendering. It bridges the
serialized job dict (extract + parser_plan as plain dicts) to the parser
pipeline, and persists self-healed selectors back to user presets
(built-ins are read-only — logged, not written). Extracted here rather than
inlined in worker_pool so it is unit-testable without spawning a process.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from src.extract.extractor import extract_fields
from src.extract.models import ExtractRule
from src.presets.materializer import strip_materializer_injected
from src.presets.models import ParserPlan, ParsingInstructions
from src.presets.parser_pipeline import run as run_pipeline
from src.presets.store import PresetStore

log = logging.getLogger(__name__)


def _get_store() -> PresetStore:
    return PresetStore()


def _persist_self_heal(
    preset_name: str,
    healed: ParsingInstructions,
    warnings: list[str],
) -> None:
    try:
        store = _get_store()
        preset = store.get(preset_name)
        updated = preset.model_copy(
            update={
                "parsing_instructions": healed,
                "version": preset.version + 1,
                "updated_at": time.time(),
            }
        )
        store.update(preset_name, updated)
        log.info(
            "self-heal persisted: preset=%s new_version=%d",
            preset_name,
            updated.version,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Persistence is best-effort: the scrape already succeeded, a failed
        # write must not fail the response.
        log.warning("self-heal persist failed for %s: %s", preset_name, exc)
        warnings.append(f"self_heal_persist_failed: {exc}")


async def apply(
    page_html: str,
    extract_dict: dict[str, Any] | None,
    plan_dict: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not extract_dict and not plan_dict:
        return None, []
    if not page_html:
        return None, []

    instructions = (
        ExtractRule.model_validate(extract_dict) if extract_dict else None
    )

    # No preset plan -> raw /scrape path: behave exactly like the old direct
    # extract so non-preset callers are unaffected.
    if not plan_dict:
        if instructions is None:
            return None, []
        data, warnings = extract_fields(page_html, instructions)
        return data, [str(warning) for warning in warnings]

    plan = ParserPlan.model_validate(plan_dict)
    result = await run_pipeline(
        page_html,
        instructions,
        self_heal=plan.self_heal,
        llm_model=plan.llm_model,
        output_schema=plan.output_schema,
        llm_extract_prompt=plan.llm_extract_prompt,
    )
    warnings = [str(warning) for warning in result.warnings]

    if result.mode == "self_healed" and result.healed_instructions:
        # Strip whatever THIS request's materialize() call injected (a price
        # locale, a urljoin base) before it can be written to a user preset —
        # `result.healed_instructions` copies post_process verbatim from the
        # already-materialized instructions, so left alone it would freeze
        # this one request's locale/URL into the preset forever. See
        # strip_materializer_injected's docstring.
        healed_for_persist = strip_materializer_injected(
            result.healed_instructions, plan.materializer_injected
        )
        if plan.preset_kind == "user" and plan.preset_name:
            _persist_self_heal(
                plan.preset_name, healed_for_persist, warnings
            )
        elif plan.preset_name:
            log.info(
                "self-heal recovered built-in preset %s but built-in presets "
                "are read-only; not persisting (regenerated selectors used "
                "for this request only)",
                plan.preset_name,
            )

    return result.data, warnings
