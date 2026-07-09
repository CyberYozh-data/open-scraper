"""Pydantic models for scraping presets.

A `Preset` bundles three things under one name:
1. A request profile (proxy, device, wait strategy, headers) — `request_defaults`.
2. Per-locale routing (URL template + country/timezone fingerprint) — `locales`.
3. A parsing recipe (deterministic CSS/XPath + optional LLM fallback) —
   `parsing_instructions`, `output_schema`, `prompt_schema`, `llm_extract_prompt`.

`Preset` is the persisted on-disk JSON object (under `data/presets/<name>.json`
for user presets, bundled `src/presets/builtin/*.json` for built-ins). The
materializer (PR-2) turns a `(Preset, params, locale)` tuple into a regular
`ScrapeRequest` that the existing job queue accepts.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.browser.geo_profile import resolve_profile
from src.extract.models import ExtractRule, FieldRule, PostProcess

# Re-export so callers can `from src.presets.models import PostProcess` without
# threading through the extract package.
__all__ = [
    "FieldRule",
    "LocaleProfile",
    "ParserPlan",
    "ParsingInstructions",
    "Preset",
    "PresetKind",
    "PresetMeta",
    "PostProcess",
    "derive_locale_profile",
]


PresetKind = Literal["builtin", "user"]

# Same shape as ExtractRule. Aliasing avoids two parallel definitions while
# letting preset-related code import a name that describes intent.
ParsingInstructions = ExtractRule

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class LocaleProfile(BaseModel):
    """Per-locale request shaping.

    `domain` is the TLD-suffix used by `url_template` (e.g. `de` → `amazon.de`).
    `country` is the ISO 3166-1 alpha-2 code routed to proxy + browser
    fingerprint. `locale`, `timezone`, `accept_language` may be omitted —
    `derive_locale_profile()` fills them from `src/browser/geo_profile.py`
    using `country` as the lookup key.
    """

    domain: str
    country: str
    locale: str | None = None
    timezone: str | None = None
    accept_language: str | None = None
    # Optional search-engine region code exposed to `url_template` as `{lr}`.
    # Used by yandex_search (Yandex's own `lr` region id, e.g. 225=Russia,
    # 213=Moscow) to localise results independently of the proxy exit country.
    lr: str | None = None


def derive_locale_profile(loc: LocaleProfile) -> LocaleProfile:
    """Return a copy with locale/timezone/accept_language filled from
    `geo_profile.resolve_profile(country)` where the user left them blank.

    Unknown countries leave the optional fields as-is (which means `None`
    propagates and the caller can fall back to a default).
    """
    geo = resolve_profile(loc.country)
    if geo is None:
        return loc
    return loc.model_copy(
        update={
            "locale": loc.locale or geo.locale,
            "timezone": loc.timezone or geo.timezone_id,
            "accept_language": loc.accept_language or geo.accept_language,
        }
    )


class Preset(BaseModel):
    """A named scraping recipe persisted as a single JSON document."""

    name: str
    source: str
    description: str = ""
    kind: PresetKind = "user"
    url_template: str | None = None
    request_defaults: dict[str, Any] = Field(default_factory=dict)
    locales: dict[str, LocaleProfile] = Field(default_factory=dict)
    default_locale: str = "us"
    parsing_instructions: ParsingInstructions | None = None
    output_schema: dict[str, Any] | None = None
    self_heal: bool = True
    prompt_schema: dict[str, Any] | None = None
    llm_extract_prompt: str | None = None
    version: int = 1
    updated_at: float

    @field_validator("name", "source")
    @classmethod
    def _snake_case(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(
                f"must be lowercase snake_case, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _default_locale_known(self) -> Preset:
        if self.locales and self.default_locale not in self.locales:
            raise ValueError(
                f"default_locale={self.default_locale!r} is not in "
                f"locales={list(self.locales)}"
            )
        return self


class PresetMeta(BaseModel):
    """Echo of which preset / locale / version was applied to a scrape.

    Returned on `ScrapeResponse.meta` so callers can confirm what the server
    actually used after preset materialization.
    """

    name: str
    source: str
    locale: str | None = None
    version: int = 1


class ParserPlan(BaseModel):
    """LLM-side parsing config carried alongside ScrapeRequest.extract.

    The deterministic selectors travel in ScrapeRequest.extract; this carries
    the bits the worker needs for self-heal / AI-only extraction. `llm_model`
    is None when no LLM is configured, which disables every LLM step. The
    preset identity lets the worker persist self-healed selectors back to a
    user preset (built-ins are read-only and only logged).
    """

    self_heal: bool = False
    llm_model: str | None = None
    output_schema: dict[str, Any] | None = None
    llm_extract_prompt: str | None = None
    preset_name: str | None = None
    preset_kind: PresetKind | None = None
