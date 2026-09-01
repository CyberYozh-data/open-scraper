from __future__ import annotations

import re
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
    "base64_decode",
    "urljoin",
    "null_if_regex",
    "unwrap_param",
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
            "string ops. 'base64_decode' decodes url-safe base64 with optional "
            "padding — pair it with 'regex' to read a destination out of a "
            "click-tracking wrapper (Bing hides every organic URL behind "
            "bing.com/ck/a?...&u=a1<base64url>). 'strip_tags' renders an HTML "
            "fragment down to its "
            "text (tags dropped, entities decoded, whitespace collapsed) — "
            "pair it with attr='html' + 'regex' to read a value out of an "
            "always-present container without the container's markup. "
            "'unwrap_param' (args=[param_name]) reads a query parameter out "
            "of a click-tracking redirect (Amazon's sponsored-result links "
            "carry the real destination inline: "
            "'/sspa/click?...&url=%2Freal%2Fpath') and percent-decodes it; "
            "when the value has no such parameter it PASSES THROUGH "
            "UNCHANGED (not null), which is what lets one pipeline handle a "
            "mixed field of wrapped and unwrapped values -- pair it with "
            "'urljoin' to also resolve the recovered (still relative) path. "
            "The decoded value is only ever handed on when it is an absolute "
            "http(s) URL with a host, or a plain relative path: the parameter "
            "is text the PAGE controls, so a crafted value is refused and the "
            "original wrapper passes through UNCHANGED (never null). Refused: "
            "any other scheme ('javascript:', 'data:', 'file:', 'ftp:' ...); "
            "an http(s) value naming no host ('http:///x'); anything opening "
            "with TWO OR MORE slashes or backslashes -- '//host/path' and "
            "every other spelling of it ('///host', '/\\host', '\\\\host'), "
            "which a browser resolves onto 'host' identically; and a "
            "reference naming no path segment of its own ('', '.', '?a=b', "
            "'#frag'), which can only point back at the page being scraped. "
            "Worst case is a passthrough, so this op cannot introduce a "
            "non-http(s) link and cannot turn a relative value into an "
            "off-host one. It is NOT a same-host guarantee: an absolute "
            "http(s) URL naming ANY host is accepted by design, because a "
            "redirect may legitimately point off-site. "
            "'url=https%3A%2F%2Fother.example%2Fx' is handed on as "
            "'https://other.example/x' and a following 'urljoin' leaves it "
            "unchanged (RFC 3986: an absolute reference ignores the base). "
            "A caller that needs the destination to stay on the page's own "
            "host must check the host itself -- this op does not do it for "
            "them, and neither does 'urljoin'. "
            "'urljoin' (args=[base_url]?) resolves a relative href (Amazon "
            "serves every search-result href relative) against base_url via "
            "RFC 3986 resolution; an already-absolute value passes through "
            "unchanged. extract_fields never sees the page's own URL, so "
            "base_url is usually left empty in the preset and injected by "
            "the materializer from the request it is about to fetch (same "
            "pattern as parse_price's empty locale arg). Unlike "
            "parse_price's 'us' default, there is no sensible default "
            "transform for a URL with no base: with none at all "
            "(materializer bypassed and no explicit base_url given) it "
            "leaves the value UNCHANGED and adds a warning, rather than "
            "silently shipping a relative link with nothing telling the "
            "caller why. 'null_if_regex' (args=[pattern]) nulls the value "
            "IN PLACE when pattern matches, leaving it untouched otherwise "
            "-- for excluding one shape from an all=true field without "
            "shrinking the array, which would misalign every later row "
            "against its sibling fields."
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
        if self.op == "unwrap_param" and len(self.args) < 1:
            raise ValueError("unwrap_param requires 1 arg: [param_name]")
        if self.op == "null_if_regex":
            if len(self.args) < 1:
                raise ValueError("null_if_regex requires 1 arg: [pattern]")
            # Unlike 'regex' (whose pattern is validated the same way it has
            # been since this op shipped, and is left alone here), an
            # uncompilable null_if_regex pattern is caught at preset-creation
            # time rather than nulling the whole column at scrape time -- the
            # cost of a self-heal-worthy typo discovered only in production.
            try:
                re.compile(self.args[0])
            except re.error as exc:
                raise ValueError(
                    f"null_if_regex: invalid regex pattern {self.args[0]!r}: {exc}"
                ) from exc
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
