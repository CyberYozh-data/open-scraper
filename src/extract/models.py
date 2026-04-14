from __future__ import annotations

from typing import Dict, Literal

from pydantic import BaseModel

ExtractType = Literal["css", "xpath"]


class FieldRule(BaseModel):
    selector: str
    attr: str = "text"
    all: bool = False
    required: bool = False


class ExtractRule(BaseModel):
    type: ExtractType
    fields: Dict[str, FieldRule]
