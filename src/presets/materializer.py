"""Turn a (Preset, PresetScrapeRequest) pair into a plain ScrapeRequest.

This runs on the API layer (B1 in the design): the preset is fully expanded
into a normal ScrapeRequest *before* it enters the existing job queue, so the
worker stays preset-agnostic. The deterministic parser already runs in the
worker via ScrapeRequest.extract — no separate parser channel is needed for
PR-2. Self-heal / AI-only execution is PR-3 and only carried as metadata here.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote, quote_plus

from pydantic import BaseModel, Field, ValidationError

from src.presets.models import (
    ParserPlan,
    ParsingInstructions,
    Preset,
    PresetMeta,
    derive_locale_profile,
)
from src.schemas import ScrapeRequest

log = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Countries that write prices with a comma decimal separator ("12,34").
# parse_price defaults to "us" (dot decimal); a built-in shipping a `de`
# locale would otherwise turn "12,34 €" into 1234.0. The set is a pragmatic
# majority — explicit args on a step always win, so a preset author can
# override per field.
_COMMA_DECIMAL_COUNTRIES = frozenset(
    {
        "DE", "FR", "ES", "IT", "NL", "BE", "AT", "PT", "FI", "SE", "NO",
        "DK", "PL", "CZ", "SK", "HU", "RO", "BG", "HR", "SI", "LT", "LV",
        "EE", "GR", "RU", "UA", "TR", "BR", "AR", "CL", "CO", "ID", "VN",
    }
)


def _price_locale_for(country: str) -> str:
    return "eu" if country.upper() in _COMMA_DECIMAL_COUNTRIES else "us"


def _inject_price_locale(
    instructions: "ParsingInstructions", price_locale: str
) -> "ParsingInstructions":
    """Fill empty parse_price args with the locale-appropriate separator
    rule. Explicit args set by the preset author are left untouched."""
    new_fields = {}
    changed = False
    for fname, frule in instructions.fields.items():
        new_pp = []
        for step in frule.post_process:
            if step.op == "parse_price" and not step.args:
                new_pp.append(step.model_copy(update={"args": [price_locale]}))
                changed = True
            else:
                new_pp.append(step)
        new_fields[fname] = (
            frule.model_copy(update={"post_process": new_pp})
            if new_pp != list(frule.post_process)
            else frule
        )
    if not changed:
        return instructions
    return instructions.model_copy(update={"fields": new_fields})


class MaterializeError(ValueError):
    """Preset could not be turned into a valid ScrapeRequest (missing
    template params, unknown locale, request_defaults that don't fit
    ScrapeRequest). API maps this to HTTP 400."""


class SessionConflictError(MaterializeError):
    """session_id given via both the field and request_override with different values.

    API maps this to HTTP 422 (vs 400 for other MaterializeError).
    """


class LLMConfig(BaseModel):
    model: str = "anthropic/claude-haiku-4-5-20251001"
    temperature: float = 0.0
    max_tokens: int = 4000


class PresetScrapeRequest(BaseModel):
    source: str = Field(..., description="Preset name to apply.")
    preset_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Values for url_template placeholders, e.g. {'asin': 'B0..'}.",
    )
    locale: str | None = Field(
        default=None,
        description="Locale key from the preset's `locales`; preset default if unset.",
    )
    parsing_override: ParsingInstructions | None = Field(
        default=None,
        description="Replace the preset's parsing_instructions for this call.",
    )
    schema_override: dict[str, Any] | None = Field(
        default=None,
        description="User JSON schema; consumed by the LLM path (PR-3).",
    )
    self_heal: bool | None = Field(
        default=None,
        description="Override preset.self_heal for this call (PR-3).",
    )
    llm: LLMConfig | None = Field(
        default=None,
        description="LLM model/params for self-heal or AI-only extraction (PR-3).",
    )
    request_override: dict[str, Any] | None = Field(
        default=None,
        description="Advanced: override any ScrapeRequest field after the preset.",
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Run this preset scrape inside a previously authenticated "
            "server-side session. Validated against the *materialized* "
            "request's device/proxy/geo: 404 if unknown, 410 if expired, "
            "422 if incompatible. If also given via request_override, the "
            "two must be equal (else 422)."
        ),
    )


def _render_template(template: str, variables: dict[str, str]) -> str:
    """Substitute {placeholders} with context-aware percent-encoding.

    Placeholders before the first '?' are URL path segments and use strict
    RFC-3986 encoding (space -> %20). Placeholders in the query string use
    application/x-www-form-urlencoded (space -> +). Values are raw here and
    encoded per-position so a single quoting strategy can't corrupt a path.
    """
    needed = set(_PLACEHOLDER.findall(template))
    missing = sorted(needed - variables.keys())
    if missing:
        raise MaterializeError(f"missing template params: {missing}")

    query_start = template.find("?")

    def _sub(match: re.Match[str]) -> str:
        raw = str(variables[match.group(1)])
        in_query = query_start != -1 and match.start() > query_start
        return quote_plus(raw) if in_query else quote(raw, safe="")

    return _PLACEHOLDER.sub(_sub, template)


def materialize(preset: Preset, req: PresetScrapeRequest) -> ScrapeRequest:
    locale_key = req.locale or preset.default_locale
    locale = preset.locales.get(locale_key)
    if locale is None:
        raise MaterializeError(
            f"unknown locale {locale_key!r}; preset offers "
            f"{sorted(preset.locales)}"
        )
    locale = derive_locale_profile(locale)

    lang = (
        locale.locale.split("-")[0].lower()
        if locale.locale
        else locale.country.lower()
    )
    template_vars: dict[str, str] = {
        "domain": locale.domain,
        "country": locale.country.lower(),
        "lang": lang,
    }
    if locale.lr is not None:
        template_vars["lr"] = locale.lr
    # preset_params last so a caller can override a locale-derived value
    # (e.g. pass a custom Yandex `lr` region).
    for key, value in req.preset_params.items():
        template_vars[key] = str(value)

    if not preset.url_template:
        raise MaterializeError(f"preset {preset.name!r} has no url_template")
    url = _render_template(preset.url_template, template_vars)

    merged: dict[str, Any] = dict(preset.request_defaults)
    if req.request_override:
        # `spoof_os` and `fingerprint_profile` are two ways to name the same
        # thing, so a caller who states one of them means to REPLACE whichever
        # the preset stated — not to be told the two disagree. Without this,
        # `spoof_os` on a preset carrying `fingerprint_profile` (both Camoufox
        # builtins do) is a 400 for what is plainly an override.
        for stated, superseded in (
            ("spoof_os", "fingerprint_profile"),
            ("fingerprint_profile", "spoof_os"),
        ):
            if stated in req.request_override and superseded not in req.request_override:
                merged.pop(superseded, None)
        merged.update(req.request_override)

    unknown = set(merged) - set(ScrapeRequest.model_fields)
    # url/extract/proxy_geo/preset_meta are set by us, not via request_defaults
    unknown -= {"url", "extract", "proxy_geo", "preset_meta"}
    if unknown:
        raise MaterializeError(
            f"request_defaults/request_override has keys not on ScrapeRequest: "
            f"{sorted(unknown)}"
        )

    # Reject an ambiguous request rather than silently preferring one
    # session_id channel (first-class field vs request_override) over the
    # other.
    override_sid = merged.get("session_id")
    if (
        req.session_id is not None
        and override_sid is not None
        and req.session_id != override_sid
    ):
        raise SessionConflictError(
            f"session_id conflict: request field {req.session_id!r} != "
            f"request_defaults/request_override {override_sid!r}"
        )
    resolved_sid = req.session_id if req.session_id is not None else override_sid
    if resolved_sid is not None:
        merged["session_id"] = resolved_sid

    if "proxy_geo" not in merged:
        merged["proxy_geo"] = {
            "country_code": locale.proxy_country or locale.country
        }
    else:
        # Escape hatch: a preset/request can pin proxy_geo independent of the
        # locale's country. Legitimate but easy to get wrong, so make the
        # decoupling visible rather than silent.
        override_geo = merged["proxy_geo"]
        override_cc = (
            override_geo.get("country_code")
            if isinstance(override_geo, dict)
            else getattr(override_geo, "country_code", None)
        )
        log.info(
            "preset %r: proxy_geo pinned to %s by request_defaults/override, "
            "decoupled from locale exit %s (market country %s)",
            preset.name,
            override_cc,
            locale.proxy_country or locale.country,
            locale.country,
        )

    instructions = req.parsing_override or preset.parsing_instructions
    if instructions is not None:
        instructions = _inject_price_locale(
            instructions, _price_locale_for(locale.country)
        )
    else:
        # AI-only preset: no deterministic parser. Force raw_html so the PR-3
        # LLM step has the page to work with.
        merged.setdefault("raw_html", True)

    preset_meta = PresetMeta(
        name=preset.name,
        source=preset.source,
        locale=locale_key,
        version=preset.version,
    )

    self_heal = req.self_heal if req.self_heal is not None else preset.self_heal
    parser_plan = ParserPlan(
        self_heal=self_heal,
        # No llm config -> model None -> every LLM step is disabled.
        llm_model=req.llm.model if req.llm else None,
        output_schema=req.schema_override or preset.output_schema,
        llm_extract_prompt=preset.llm_extract_prompt,
        preset_name=preset.name,
        preset_kind=preset.kind,
    )

    try:
        return ScrapeRequest(
            url=url,
            extract=instructions,
            preset_meta=preset_meta,
            parser_plan=parser_plan,
            **merged,
        )
    except ValidationError as exc:
        raise MaterializeError(
            f"preset {preset.name!r} produced an invalid ScrapeRequest: {exc}"
        ) from exc
