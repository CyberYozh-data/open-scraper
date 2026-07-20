from __future__ import annotations

from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, model_validator

ExtractType = Literal["css", "xpath"]

PostProcessOp = Literal[
    "regex",
    "strip",
    "strip_tags",
    "parse_int",
    "parse_float",
    "parse_price",
    "lowercase",
    "uppercase",
    "replace",
]


class PostProcess(BaseModel):
    op: PostProcessOp = Field(
        ...,
        description=(
            "Transform applied to the extracted value. Steps run in order, "
            "each fed the previous result. 'regex' (args=[pattern, group?]) "
            "returns a capture group; 'parse_price' (args=['us'|'eu']?) and "
            "'parse_int'/'parse_float' coerce to numbers; 'strip' (args=[chars]?), "
            "'lowercase', 'uppercase', and 'replace' (args=[old, new]) are "
            "string ops. 'strip_tags' renders an HTML fragment down to its "
            "text (tags dropped, entities decoded, whitespace collapsed) — "
            "pair it with attr='html' + 'regex' to read a value out of an "
            "always-present container without the container's markup."
        ),
    )
    args: list[Any] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_args(self) -> PostProcess:
        # Catch obviously broken op/args combinations at preset-creation time
        # rather than per-row at scrape time. Ops without an arity check
        # accept anything (and ignore extras).
        if self.op == "replace" and len(self.args) < 2:
            raise ValueError("replace requires 2 args: [old, new]")
        if self.op == "regex" and len(self.args) < 1:
            raise ValueError("regex requires 1 arg: [pattern]")
        if self.op == "parse_price":
            if self.args and self.args[0] not in ("us", "eu"):
                raise ValueError(
                    "parse_price locale must be 'us' or 'eu', "
                    f"got {self.args[0]!r}"
                )
        return self


class FieldRule(BaseModel):
    selector: str = Field(
        ...,
        description=(
            "CSS or XPath expression. Defaults to the parent ExtractRule.type "
            "unless `type` below overrides it per-field. Examples: 'h1', "
            "'.price_color', '#cart a' for CSS; '//h1', "
            "'//div[@class=\"item\"]/a/@href' for XPath."
        ),
    )
    type: ExtractType | None = Field(
        default=None,
        description=(
            "Override the parent ExtractRule.type for just this field. Lets a "
            "single rule mix CSS and XPath selectors. Defaults to the rule "
            "type when unset."
        ),
    )
    attr: str = Field(
        default="text",
        description=(
            "What to pull from the matched element. One of: "
            "'text' (default, text content), 'html' (outer HTML of the node), "
            "or any HTML attribute name like 'href', 'src', 'data-id'."
        ),
    )
    all: bool = Field(
        default=False,
        description=(
            "If false (default), returns the FIRST match as a string. If true, "
            "returns a LIST of every match. Use true for repeating elements "
            "like product cards, links, table rows."
        ),
    )
    required: bool = Field(
        default=False,
        description=(
            "If true and the selector matches nothing, a warning is added to "
            "the response. The request itself still succeeds."
        ),
    )
    post_process: list[PostProcess] = Field(
        default_factory=list,
        description=(
            "Ordered list of transforms applied to the matched value(s) "
            "before they land in the response. Applied per-item when all=true."
        ),
    )


class ExtractRule(BaseModel):
    type: ExtractType = Field(
        ...,
        description=(
            "Default selector language for every field: 'css' "
            "(lxml.cssselect) or 'xpath' (lxml XPath). A field may override "
            "this via its own `type`."
        ),
    )
    fields: Dict[str, FieldRule] = Field(
        ...,
        description=(
            "Map of {output_key: FieldRule}. The output_key is the name the "
            "extracted value will appear under in the response's data object. "
            "Example: {'title': {selector:'h1'}, 'price': {selector:'.price_color'}} "
            "-> data = {'title': '...', 'price': '...'}."
        ),
    )
