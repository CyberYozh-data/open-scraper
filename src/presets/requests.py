from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PresetGenerateRequest(BaseModel):
    name: str
    mode: Literal["manual", "from_schema", "from_prompt"]
    # manual
    preset: dict[str, Any] | None = None
    # from_schema / from_prompt
    source: str | None = None
    sample_url: str | None = None
    sample_html: str | None = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    description: str | None = None
    llm_model: str | None = None

    model_config = {"populate_by_name": True}


class PresetTestRequest(BaseModel):
    sample_url: str | None = None
    sample_html: str | None = None
    llm_model: str | None = None
    self_heal: bool = False


class PresetPreviewRequest(BaseModel):
    mode: Literal["manual", "from_schema", "from_prompt"]
    sample_url: str | None = None
    sample_html: str | None = None
    parsing_instructions: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    llm_extract_prompt: str | None = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    description: str | None = None
    llm_model: str | None = None
    self_heal: bool = False

    model_config = {"populate_by_name": True}
