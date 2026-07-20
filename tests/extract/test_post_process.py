from __future__ import annotations

import pytest

from src.extract.extractor import extract_fields
from src.extract.models import ExtractRule, FieldRule, PostProcess


HTML_PRICE = "<div class='p'>$19.99 USD</div>"
HTML_TITLE = "<h1>  Hello   World  </h1>"
HTML_LIST = """
<ul>
  <li class='x'>foo</li>
  <li class='x'>bar</li>
  <li class='x'>baz</li>
</ul>
"""


class TestRegexOp:
    def test_extracts_first_group(self):
        rule = ExtractRule(
            type="css",
            fields={
                "price": FieldRule(
                    selector=".p",
                    post_process=[PostProcess(op="regex", args=[r"\$([\d.]+)"])],
                ),
            },
        )
        data, warnings = extract_fields(HTML_PRICE, rule)
        assert data["price"] == "19.99"
        assert warnings == []

    def test_invalid_pattern_emits_warning(self):
        rule = ExtractRule(
            type="css",
            fields={
                "price": FieldRule(
                    selector=".p",
                    post_process=[PostProcess(op="regex", args=["[unterminated"])],
                ),
            },
        )
        data, warnings = extract_fields(HTML_PRICE, rule)
        assert data["price"] is None
        assert any("regex" in w for w in warnings)

    def test_no_match_returns_none(self):
        rule = ExtractRule(
            type="css",
            fields={
                "price": FieldRule(
                    selector=".p",
                    post_process=[PostProcess(op="regex", args=[r"BOGUS(\d+)"])],
                ),
            },
        )
        data, warnings = extract_fields(HTML_PRICE, rule)
        assert data["price"] is None


def _price_rule(*args):
    return ExtractRule(
        type="css",
        fields={
            "price": FieldRule(
                selector=".p",
                post_process=[PostProcess(op="parse_price", args=list(args))],
            ),
        },
    )


class TestParsePriceOp:
    def test_extracts_decimal_default_us(self):
        data, _ = extract_fields(HTML_PRICE, _price_rule())
        assert data["price"] == 19.99

    def test_explicit_us_locale(self):
        data, _ = extract_fields("<div class='p'>1,234.56</div>", _price_rule("us"))
        assert data["price"] == 1234.56

    def test_explicit_eu_locale(self):
        data, _ = extract_fields("<div class='p'>1.299,50 EUR</div>", _price_rule("eu"))
        assert data["price"] == 1299.50

    def test_both_separators_use_position_regardless_of_locale_arg(self):
        # Both separators present — last one wins (unambiguous), locale ignored.
        data, _ = extract_fields("<div class='p'>1.299,50</div>", _price_rule("us"))
        assert data["price"] == 1299.50
        data, _ = extract_fields("<div class='p'>1,234.56</div>", _price_rule("eu"))
        assert data["price"] == 1234.56

    def test_single_separator_three_digit_tail_us_treats_as_thousands(self):
        data, _ = extract_fields("<div class='p'>1,234</div>", _price_rule("us"))
        assert data["price"] == 1234.0

    def test_single_separator_three_digit_tail_eu_treats_as_thousands(self):
        data, _ = extract_fields("<div class='p'>1.234</div>", _price_rule("eu"))
        assert data["price"] == 1234.0

    def test_negative_price_preserved(self):
        data, _ = extract_fields("<div class='p'>-19.99</div>", _price_rule())
        assert data["price"] == -19.99

    def test_unparseable_returns_none(self):
        data, _ = extract_fields("<div class='p'>price unavailable</div>", _price_rule())
        assert data["price"] is None


class TestParseIntOp:
    def test_parse_int_strips_non_digits(self):
        html = "<span>(1,234) reviews</span>"
        rule = ExtractRule(
            type="css",
            fields={
                "n": FieldRule(
                    selector="span",
                    post_process=[PostProcess(op="parse_int")],
                ),
            },
        )
        data, _ = extract_fields(html, rule)
        assert data["n"] == 1234


class TestStringOps:
    def test_strip(self):
        rule = ExtractRule(
            type="css",
            fields={
                "t": FieldRule(
                    selector="h1",
                    post_process=[PostProcess(op="strip")],
                ),
            },
        )
        data, _ = extract_fields(HTML_TITLE, rule)
        assert data["t"] == "Hello World"

    def test_lowercase(self):
        rule = ExtractRule(
            type="css",
            fields={
                "t": FieldRule(
                    selector="h1",
                    post_process=[PostProcess(op="lowercase")],
                ),
            },
        )
        data, _ = extract_fields(HTML_TITLE, rule)
        assert data["t"] == "hello world"

    def test_uppercase(self):
        rule = ExtractRule(
            type="css",
            fields={
                "t": FieldRule(
                    selector="h1",
                    post_process=[PostProcess(op="uppercase")],
                ),
            },
        )
        data, _ = extract_fields(HTML_TITLE, rule)
        assert data["t"] == "HELLO WORLD"

    def test_replace(self):
        rule = ExtractRule(
            type="css",
            fields={
                "t": FieldRule(
                    selector="h1",
                    post_process=[PostProcess(op="replace", args=["World", "Earth"])],
                ),
            },
        )
        data, _ = extract_fields(HTML_TITLE, rule)
        assert data["t"] == "Hello Earth"


class TestStripTagsOp:
    """strip_tags exists so a field can anchor to an always-present container
    (attr='html' + regex) and still return text — see google_search.snippets.
    """

    def _run(self, html: str, ops: list[PostProcess], attr: str = "text"):
        rule = ExtractRule(
            type="css",
            fields={"t": FieldRule(selector=".p", attr=attr, post_process=ops)},
        )
        return extract_fields(html, rule)

    def test_drops_tags_decodes_entities_collapses_whitespace(self):
        html = "<div class='p'><span>a &amp; <em>b</em>\n   c</span></div>"
        data, warnings = self._run(html, [PostProcess(op="strip_tags")], attr="html")
        assert data["t"] == "a & b c"
        assert warnings == []

    def test_matches_attr_text_for_the_same_node(self):
        """The whole point: html+strip_tags == what attr='text' returned."""
        html = "<div class='p'>x <em>y</em>  &amp;  <span>z</span></div>"
        as_text, _ = self._run(html, [])
        as_html, _ = self._run(html, [PostProcess(op="strip_tags")], attr="html")
        assert as_html["t"] == as_text["t"] == "x y & z"

    def test_handles_unbalanced_fragment_from_a_regex_slice(self):
        # A regex slice of a container's html is rarely well-formed; stray
        # close tags must not blow up or leak.
        html = "<div class='p'><span>text</span></div></div></div></div>"
        data, warnings = self._run(html, [PostProcess(op="strip_tags")], attr="html")
        assert data["t"] == "text"
        assert warnings == []

    def test_plain_text_passthrough_is_whitespace_collapsed(self):
        html = "<div class='p'>  plain   text  </div>"
        data, _ = self._run(html, [PostProcess(op="strip_tags")])
        assert data["t"] == "plain text"

    def test_none_from_earlier_op_stays_none(self):
        # regex miss -> None -> strip_tags must not coerce it to "None".
        html = "<div class='p'>no digits here</div>"
        data, _ = self._run(
            html,
            [PostProcess(op="regex", args=[r"(\d+)"]), PostProcess(op="strip_tags")],
            attr="html",
        )
        assert data["t"] is None


class TestLogSpamDedupe:
    def test_invalid_regex_emits_single_warning_for_list_field(self):
        rule = ExtractRule(
            type="css",
            fields={
                "items": FieldRule(
                    selector="li.x",
                    all=True,
                    post_process=[PostProcess(op="regex", args=["[unterminated"])],
                ),
            },
        )
        data, warnings = extract_fields(HTML_LIST, rule)
        assert data["items"] == [None, None, None]
        # 3 elements, 1 warning (deduped per field+op)
        regex_warnings = [w for w in warnings if "regex" in w]
        assert len(regex_warnings) == 1


class TestPipelineChaining:
    def test_regex_then_parse_float(self):
        rule = ExtractRule(
            type="css",
            fields={
                "price": FieldRule(
                    selector=".p",
                    post_process=[
                        PostProcess(op="regex", args=[r"\$([\d.]+)"]),
                        PostProcess(op="parse_float"),
                    ],
                ),
            },
        )
        data, _ = extract_fields(HTML_PRICE, rule)
        assert data["price"] == 19.99

    def test_pipeline_applies_per_item_when_all_true(self):
        rule = ExtractRule(
            type="css",
            fields={
                "items": FieldRule(
                    selector="li.x",
                    all=True,
                    post_process=[PostProcess(op="uppercase")],
                ),
            },
        )
        data, _ = extract_fields(HTML_LIST, rule)
        assert data["items"] == ["FOO", "BAR", "BAZ"]


class TestPerFieldTypeOverride:
    def test_field_type_overrides_rule_type(self):
        html = "<div><h2 class='x'>via-css</h2><span class='x'>via-xpath</span></div>"
        rule = ExtractRule(
            type="css",
            fields={
                "css_pick": FieldRule(selector="h2.x"),
                "xpath_pick": FieldRule(selector="//span[@class='x']/text()", type="xpath"),
            },
        )
        data, warnings = extract_fields(html, rule)
        assert data["css_pick"] == "via-css"
        assert data["xpath_pick"] == "via-xpath"
        assert warnings == []


class TestReplaceValidation:
    def test_replace_with_fewer_than_two_args_rejected_at_model_time(self):
        with pytest.raises(Exception):  # pydantic.ValidationError
            PostProcess(op="replace", args=["only_one"])


class TestBackwardCompat:
    def test_empty_post_process_behaves_like_before(self):
        rule = ExtractRule(
            type="css",
            fields={"title": FieldRule(selector="h1")},
        )
        data, _ = extract_fields(HTML_TITLE, rule)
        assert data["title"] == "Hello World"
