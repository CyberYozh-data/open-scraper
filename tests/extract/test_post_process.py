from __future__ import annotations

from urllib.parse import urlsplit

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


class TestUrljoinOp:
    """`urljoin` exists so a relative href (amazon_search reads raw `href`,
    which Amazon serves relative -- `/dp/ASIN/ref=...`) can be resolved into
    something a caller can actually follow. The extractor never sees the page
    URL (extract_fields takes only page_html), so the base has to be supplied
    as an explicit arg -- materializer.py injects it per-locale the same way
    it injects parse_price's locale (see TestUrlBaseInjection in
    tests/presets/test_materializer.py)."""

    def _run(self, href: str, *base_args: str):
        rule = ExtractRule(
            type="css",
            fields={
                "url": FieldRule(
                    selector="a",
                    attr="href",
                    post_process=[PostProcess(op="urljoin", args=list(base_args))],
                ),
            },
        )
        return extract_fields(f'<a href="{href}">x</a>', rule)

    def test_resolves_a_relative_path_against_the_base(self):
        data, warnings = self._run(
            "/dp/B0F8L98RLY/ref=sr_1_3", "https://www.amazon.de/s?k=laptop"
        )
        assert data["url"] == "https://www.amazon.de/dp/B0F8L98RLY/ref=sr_1_3"
        assert warnings == []

    def test_an_already_absolute_url_passes_through_unchanged(self):
        data, _ = self._run(
            "https://www.amazon.de/dp/OTHER", "https://www.amazon.de/s?k=laptop"
        )
        assert data["url"] == "https://www.amazon.de/dp/OTHER"

    def test_no_base_leaves_the_value_unchanged_but_warns(self):
        """Fix-round-1 finding 4: unlike parse_price's empty-args default
        (which still performs its transform with a documented "us"
        fallback), there is no sensible default transform for a URL with no
        base -- so a preset author who forgot to have the base injected
        gets the untouched value back AND a warning naming the cause,
        instead of a silently-relative "success"."""
        data, warnings = self._run("/dp/B0F8L98RLY/ref=sr_1_3")
        assert data["url"] == "/dp/B0F8L98RLY/ref=sr_1_3"
        assert len(warnings) == 1
        assert "urljoin" in warnings[0] and "no base_url" in warnings[0]


class TestUnwrapParamOp:
    """`unwrap_param` recovers a redirect's real destination from one of its
    own query parameters -- built for Amazon's sponsored `/sspa/click`
    redirects, which carry the destination inline (`url=%2Freal%2Fpath`)
    rather than behind an opaque token (contrast Bing's `bing.com/ck/a`,
    which needs `base64_decode`). The pass-through-on-no-match behaviour is
    the point: it lets one pipeline handle a field mixing wrapped (sponsored)
    and unwrapped (organic) values without a separate branch per shape."""

    def _run(self, href: str, param: str = "url"):
        rule = ExtractRule(
            type="css",
            fields={
                "url": FieldRule(
                    selector="a",
                    attr="href",
                    post_process=[PostProcess(op="unwrap_param", args=[param])],
                ),
            },
        )
        return extract_fields(f'<a href="{href}">x</a>', rule)

    def test_recovers_the_percent_decoded_destination(self):
        data, warnings = self._run(
            "/sspa/click?ie=UTF8&spc=AAA&url=%2FAcer-Aspire%2Fdp%2FB0836QR869"
        )
        assert data["url"] == "/Acer-Aspire/dp/B0836QR869"
        assert warnings == []

    def test_a_nested_query_string_inside_the_param_survives_decoding(self):
        # The wrapped destination's OWN query string is escaped as %26 inside
        # the outer `url=` value -- decoding must not stop at the first `&`.
        data, _ = self._run(
            "/sspa/click?ie=UTF8&url=%2Fdp%2FX%2Fref%3Dsr_1_1_sspa%26qid%3D1"
            "&aref=OUTER_TRACKING_PARAM"
        )
        assert data["url"] == "/dp/X/ref=sr_1_1_sspa&qid=1"

    def test_a_value_without_the_param_passes_through_unchanged(self):
        """The organic shape: no `url=` param at all -- must come back
        exactly as extracted, not null, so a mixed field's organic rows are
        untouched by the sponsored-only unwrap."""
        data, warnings = self._run("/dp/B0F8L98RLY/ref=sr_1_3?dib=abc&qid=1")
        assert data["url"] == "/dp/B0F8L98RLY/ref=sr_1_3?dib=abc&qid=1"
        assert warnings == []

    def test_a_different_param_name_is_configurable(self):
        data, _ = self._run("/r?dest=%2Fp%2F1", param="dest")
        assert data["url"] == "/p/1"

    def test_requires_a_param_name_at_model_time(self):
        with pytest.raises(Exception):  # pydantic.ValidationError
            PostProcess(op="unwrap_param")

    def test_decode_depth_is_exactly_one_not_two(self):
        """Fix-round-2 finding 4 (surviving mutant): `unquote()` must run
        EXACTLY once. A verbatim real sponsored href from the 2026-08-27
        audit (research/preset_audit_dual_engine_2026_08_27.json) wraps a
        product whose own slug contains a doubly-percent-encoded `®`
        (`Snapdragon%25C2%25AE`) -- Amazon's real destination path carries
        the LITERAL substring `%C2%AE`, still percent-encoded, not the
        decoded `®` character. `unquote(unquote(...))` passes every other
        test in this class (all of them decode-idempotent) but corrupts
        exactly this shape into a raw, wrong character."""
        href = (
            "/sspa/click?ie=UTF8&spc=MTo0MzAzNjE1MzMzNDczNTYwOjE3ODc4ODAzMzA6"
            "c3BfbXRmOjMwMTI5NzIwNTA5NjUzMjo6MDo6&url=%2FMicrosoft-Touchscreen-"
            "Snapdragon%25C2%25AE-Speicher-Neuestes%2Fdp%2FB0DYDXH5BR%2Fref%3D"
            "sr_1_14_sspa%3Fdib%3DeyJ2IjoiMSJ9.abc%26qid%3D1&aref=TRACK"
        )
        data, _ = self._run(href)
        assert data["url"] == (
            "/Microsoft-Touchscreen-Snapdragon%C2%AE-Speicher-Neuestes"
            "/dp/B0DYDXH5BR/ref=sr_1_14_sspa?dib=eyJ2IjoiMSJ9.abc&qid=1"
        )
        # The mutant this pins: a second unquote() turns the still-encoded
        # %C2%AE into the raw two-byte UTF-8 sequence for "®" -- assert that
        # did NOT happen, spelled out so the failure is legible on its own.
        assert "%C2%AE" in data["url"]
        assert "®" not in data["url"]

    def test_the_boundary_before_the_param_name_is_required(self):
        """Fix-round-2 finding 4 (surviving mutant): the `(?:^|[?&])` anchor
        in front of `{param}=` is the op's stated contract, not incidental --
        dropping it makes `unwrap_param("url")` also match the TAIL of
        `pd_rd_url=` or `redirect_url=`, two real Amazon/redirect query
        parameter names that end in `url=` but are not the wrapper this op
        exists to unwrap. Harmless on today's shipped data (neither name
        appears in amazon_search's actual hrefs), but the op is generic
        preset-authoring surface and the anchor is what makes "unwrap the
        `url` parameter" mean what it says."""
        data, _ = self._run("/x?pd_rd_url=%2Fother%2Fpath&y=1")
        assert data["url"] == "/x?pd_rd_url=%2Fother%2Fpath&y=1"


class TestUnwrapParamRefusesNonFollowableSchemes:
    """The decoded parameter is text the PAGE controls, and `unwrap_param` is
    published in the OpenAPI schema as a general op any user preset may use on
    an API with no auth. Every value below was confirmed to unwrap verbatim
    before this guard, landing a non-http(s) link in a field whose preset
    description calls it "a real, followable destination", with `urljoin` and
    `null_if_regex` passing it on unchanged.

    The refusal is fail-SAFE, matching `src/api/search.py:_unwrap_redirect`:
    the ORIGINAL wrapper value passes through unchanged rather than becoming
    null, so a refused row keeps its slot (row alignment) and stays visibly a
    wrapper instead of masquerading as a destination."""

    WRAPPER = "/sspa/click?ie=UTF8&spc=AAA&url="

    def _run(self, href: str):
        rule = ExtractRule(
            type="css",
            fields={
                "url": FieldRule(
                    selector="a",
                    attr="href",
                    post_process=[PostProcess(op="unwrap_param", args=["url"])],
                ),
            },
        )
        return extract_fields(f'<a href="{href}">x</a>', rule)

    @pytest.mark.parametrize(
        "encoded,decoded",
        [
            ("javascript%3Aalert(document.cookie)", "javascript:alert(document.cookie)"),
            ("data%3Atext%2Fhtml%3Bbase64%2CPHNjcmlwdD4x", "data:text/html;base64,PHNjcmlwdD4x"),
            ("file%3A%2F%2F%2Fetc%2Fpasswd", "file:///etc/passwd"),
            ("JaVaScRiPt%3Aalert(1)", "JaVaScRiPt:alert(1)"),
            # Authority-BEARING non-http schemes. Load-bearing: every case
            # above is authority-LESS, so with only those the allow-list
            # `scheme in ("http", "https")` can be weakened to `bool(netloc)`
            # and the suite stays green while ftp/ws/file-over-a-host sail
            # through.
            ("ftp%3A%2F%2Fevil.com%2Fx", "ftp://evil.com/x"),
            ("ws%3A%2F%2Fevil.com%2Fx", "ws://evil.com/x"),
            ("file%3A%2F%2Fhost%2Fshare", "file://host/share"),
            # http(s) WITHOUT an authority: it names no host, so it is not the
            # absolute URL it looks like. Pins the `and bool(parts.netloc)`
            # conjunct, which nothing else fails without.
            ("http%3A%2F%2F%2Fetc%2Fpasswd", "http:///etc/passwd"),
            ("http%3A%2Ffoo", "http:/foo"),
        ],
        ids=[
            "javascript", "data", "file", "mixed-case-javascript",
            "ftp-with-host", "ws-with-host", "file-with-host",
            "http-without-authority", "http-single-slash",
        ],
    )
    def test_a_non_http_scheme_is_refused_and_the_wrapper_survives(
        self, encoded, decoded
    ):
        href = self.WRAPPER + encoded
        data, warnings = self._run(href)
        assert data["url"] == href, "the original wrapper must pass through"
        assert data["url"] != decoded, "the crafted scheme must not be unwrapped"
        assert warnings == []

    def test_a_protocol_relative_value_is_refused(self):
        """`//evil.example.com/x` has no scheme but carries an AUTHORITY, so it
        is not a relative path: the `urljoin` step amazon_search pairs with this
        op resolves it to `https://evil.example.com/x`, off the page's own host
        entirely. Asserted through that real two-step pipeline, not on the
        unwrap alone, because the promotion only happens once urljoin runs."""
        href = self.WRAPPER + "%2F%2Fevil.example.com%2Fx"
        rule = ExtractRule(
            type="css",
            fields={
                "url": FieldRule(
                    selector="a",
                    attr="href",
                    post_process=[
                        PostProcess(op="unwrap_param", args=["url"]),
                        PostProcess(op="urljoin", args=["https://www.amazon.de/s?k=x"]),
                    ],
                ),
            },
        )
        data, _ = extract_fields(f'<a href="{href}">x</a>', rule)
        # The host, not a substring search: `evil.example.com` legitimately
        # remains inside the wrapper's own (still percent-encoded) query
        # string -- what must not happen is it becoming the URL's authority.
        assert urlsplit(data["url"]).netloc == "www.amazon.de"
        assert data["url"] == "https://www.amazon.de" + href

    @pytest.mark.parametrize(
        "encoded,decoded",
        [
            ("%2F%2F%2Fevil.example.com%2Fx", "///evil.example.com/x"),
            ("%2F%2F%2F%2Fevil.example.com%2Fx", "////evil.example.com/x"),
            ("%2F%5Cevil.example.com%2Fx", "/\\evil.example.com/x"),
            ("%5C%5Cevil.example.com%2Fx", "\\\\evil.example.com/x"),
            ("%5C%2Fevil.example.com%2Fx", "\\/evil.example.com/x"),
        ],
        ids=["three-slashes", "four-slashes", "slash-backslash",
             "two-backslashes", "backslash-slash"],
    )
    def test_every_spelling_of_a_leading_authority_is_refused(
        self, encoded, decoded
    ):
        """Fix-round-2 finding 1. `urlsplit` populates `netloc` for EXACTLY two
        leading slashes, so a `netloc`-only check refuses `//host` and accepts
        every other spelling of it. A WHATWG parser does not agree: its
        "special authority ignore slashes" state skips a whole run of `/` AND
        `\\`, so `new URL('///host/x', 'https://www.amazon.de/s?k=x')` is
        `https://host/x` -- and that parser is what a browser href, a
        Playwright navigation or any JS consumer of this field runs.

        Verified against the shipped decode path (the values below really are
        what `unwrap_param` sees). All five were measured ACCEPTED on the
        parent commit df89d64 and refused here -- re-checked in fix round 3 by
        running this test with the parent's guard monkeypatched in: 5 failed."""
        href = self.WRAPPER + encoded
        data, warnings = self._run(href)
        assert data["url"] == href, "the original wrapper must pass through"
        assert data["url"] != decoded
        assert warnings == []

    @pytest.mark.parametrize(
        "encoded,decoded",
        [
            ("%2F%09%5Cevil.example.com%2Fx", "/\t\\evil.example.com/x"),
            ("%5C%09%2Fevil.example.com%2Fx", "\\\t/evil.example.com/x"),
            ("%2F%0A%5Cevil.example.com%2Fx", "/\n\\evil.example.com/x"),
            ("%2F%0D%5Cevil.example.com%2Fx", "/\r\\evil.example.com/x"),
            ("%20%20%2F%5Cevil.example.com%2Fx", "  /\\evil.example.com/x"),
            ("%01%2F%5Cevil.example.com%2Fx", "\x01/\\evil.example.com/x"),
        ],
        ids=["tab-between-slashes", "backslash-tab-slash",
             "newline-between-slashes", "cr-between-slashes",
             "leading-spaces", "leading-control-0x01"],
    )
    def test_an_authority_hidden_behind_whitespace_is_refused(
        self, encoded, decoded
    ):
        """Fix-round-3 finding 2: the whitespace half of `_leading_slash_run`
        was unpinned. Its only case was `"  //evil.example.com/x"`, which is
        defended twice over -- `urlsplit` lstrips the spaces itself AND the
        `parts.netloc` fallback catches the resulting `//host` -- so it passed
        on the parent commit and pinned nothing.

        Every case here pairs the whitespace with a MIXED slash/backslash run.
        `urlsplit` never reads `\\` as an authority marker, so `netloc` is `''`
        for all six and the fallback cannot rescue them: the ONLY thing that
        refuses them is `_leading_slash_run` deleting tab/CR/LF and trimming
        C0-controls-and-space before it counts. A browser's parser does the
        same deletions, then its "special authority ignore slashes" state
        lands every one of them on `evil.example.com`.

        Measured: all six were ACCEPTED on the parent commit df89d64 (verified
        by running this test with the parent's guard monkeypatched in) and are
        refused here. Between them they pin every piece: `\t` (cases 1-2),
        `\n`/`\r` (3-4) in the delete set, the space at 0x20 (5) -- which
        `range(0x21)` -> `range(0x20)` would drop -- and a C0 control (6)."""
        href = self.WRAPPER + encoded
        data, warnings = self._run(href)
        assert data["url"] == href, "the original wrapper must pass through"
        assert data["url"] != decoded
        assert warnings == []

    def test_a_value_urlsplit_cannot_parse_is_refused(self):
        """The `except ValueError` arm had no test at all; mutating it to
        `return True` survived the suite. It is reachable and it matters: a
        FULLWIDTH SOLIDUS in the authority makes `urlsplit` raise on its NFKC
        check ("contains invalid characters under NFKC normalization"), and a
        browser's parser rejects the same string. Unparseable is not a
        destination -- refuse it rather than shipping a host nobody agrees on."""
        encoded = "https%3A%2F%2Fwww.amazon.de%EF%BC%8Fevil.example.com%2Fx"
        href = self.WRAPPER + encoded
        data, _ = self._run(href)
        assert data["url"] == href

    @pytest.mark.parametrize(
        "encoded,decoded",
        [("", ""), ("%2E", "."), ("%2E%2E", ".."), ("%23frag", "#frag"),
         ("%3Fa%3Db", "?a=b"), ("%2F", "/")],
        ids=["empty", "dot", "dot-dot", "fragment", "query-only", "root"],
    )
    def test_a_reference_naming_no_path_segment_is_refused(
        self, encoded, decoded
    ):
        """A relative reference with no path segment of its own resolves back
        into the page's own url -- `""`, `?a=b` and `#frag` onto the page BEING
        SCRAPED, `.`/`..`/`/` onto one of its ancestors. None is a destination
        the redirect parameter supplied, and accepting one would put the search
        page itself in an unwrapped row's product link. The `""` arm shipped
        with the guard but had no test; the rest are the same class."""
        href = self.WRAPPER + encoded
        data, _ = self._run(href)
        assert data["url"] == href
        assert data["url"] != decoded

    def test_a_relative_path_with_one_leading_slash_is_not_over_refused(self):
        """The counterweight to the slash-run check: exactly ONE leading slash
        is the shape every real Amazon sponsored destination has, and a
        backslash anywhere AFTER the first segment is an ordinary path
        character, not an authority."""
        assert self._run(self.WRAPPER + "%2Fdp%2FA%5CB")[0]["url"] == "/dp/A\\B"
        assert self._run(self.WRAPPER + "%2F-%2Fen%2Fdp%2FX")[0]["url"] == "/-/en/dp/X"

    def test_a_legitimate_relative_path_still_unwraps(self):
        """The guard must not close the op it exists for: a real sponsored
        href from the 2026-08-27 audit still yields its relative product path.
        Without this the four refusal tests above pass with `unwrap_param`
        reduced to `return value`."""
        data, warnings = self._run(
            "/sspa/click?ie=UTF8&spc=AAA&url=%2FAcer-Aspire%2Fdp%2FB0836QR869"
        )
        assert data["url"] == "/Acer-Aspire/dp/B0836QR869"
        assert warnings == []

    def test_an_absolute_http_destination_still_unwraps(self):
        data, _ = self._run(self.WRAPPER + "https%3A%2F%2Fwww.amazon.de%2Fdp%2FX")
        assert data["url"] == "https://www.amazon.de/dp/X"


class TestNullIfRegexOp:
    """`null_if_regex` nulls a value IN PLACE when it matches a pattern,
    rather than removing it -- the only shape that keeps a row-aligned
    `all=true` field the same length as its neighbours. Built for
    amazon_search: a sponsored `/sspa/click?...` href sits in the same
    result-card container as organic hrefs and is indistinguishable except
    by that shape."""

    def _run(self, href: str, pattern: str):
        rule = ExtractRule(
            type="css",
            fields={
                "url": FieldRule(
                    selector="a",
                    attr="href",
                    post_process=[PostProcess(op="null_if_regex", args=[pattern])],
                ),
            },
        )
        return extract_fields(f'<a href="{href}">x</a>', rule)

    def _run_mixed(self, hrefs: list[str], pattern: str, *extra_ops: PostProcess):
        # A single all=true field with BOTH a matching and a non-matching href
        # -- realistic amazon_search shape (one sponsored row among organic
        # ones). A field where literally every value matches is a different,
        # legitimately-warned case: `_warn_on_silent_nulls` flags a post_process
        # pipeline that nulls 100% of a non-empty column as possible markup
        # drift, and null_if_regex nulling every row (every result sponsored)
        # is indistinguishable from that at the engine level. That guard is
        # exercised on its own in TestSilentNullGuardTreatsIntentionalNulls
        # below; this fixture stays representative of ordinary pages.
        rule = ExtractRule(
            type="css",
            fields={
                "url": FieldRule(
                    selector="a",
                    attr="href",
                    all=True,
                    post_process=[PostProcess(op="null_if_regex", args=[pattern]), *extra_ops],
                ),
            },
        )
        anchors = "".join(f'<a href="{href}">x</a>' for href in hrefs)
        return extract_fields(anchors, rule)

    def test_matching_value_becomes_none(self):
        data, warnings = self._run_mixed(
            ["/sspa/click?ie=UTF8&url=%2Fdp%2FX", "/dp/OTHER/ref=sr_1"],
            r"^/sspa/click",
        )
        assert data["url"] == [None, "/dp/OTHER/ref=sr_1"]
        assert warnings == []

    def test_non_matching_value_passes_through_unchanged(self):
        data, _ = self._run("/dp/B0F8L98RLY/ref=sr_1_3", r"^/sspa/click")
        assert data["url"] == "/dp/B0F8L98RLY/ref=sr_1_3"

    def test_downstream_urljoin_sees_the_null_and_skips(self):
        """The pipeline ordering the preset relies on: null_if_regex runs
        BEFORE urljoin, so a nulled sponsored row never reaches urljoin at
        all (a None short-circuits the rest of the pipeline), while the
        organic sibling in the same field still gets resolved."""
        data, warnings = self._run_mixed(
            ["/sspa/click?url=%2Fdp%2FX", "/dp/B0F8L98RLY/ref=sr_1_3"],
            r"^/sspa/click",
            PostProcess(op="urljoin", args=["https://www.amazon.de/s?k=x"]),
        )
        assert data["url"] == [None, "https://www.amazon.de/dp/B0F8L98RLY/ref=sr_1_3"]
        assert warnings == []

    def test_requires_a_pattern_at_model_time(self):
        with pytest.raises(Exception):  # pydantic.ValidationError
            PostProcess(op="null_if_regex")

    def test_uncompilable_pattern_rejected_at_model_time(self):
        """Fix-round-1 finding 5: unlike `regex` (whose pattern is not
        compile-checked, and is left alone here -- see TestRegexOp), an
        uncompilable null_if_regex pattern is caught when the preset is
        created, not discovered by nulling the whole column at scrape time."""
        with pytest.raises(Exception):  # pydantic.ValidationError
            PostProcess(op="null_if_regex", args=["[unterminated"])


class TestNullIfRegexAllMatchedTripsSilentNullGuard:
    """Pinned so it reads as intended, not discovered by accident: when every
    value in the column matches the pattern (e.g. a page where somehow every
    result is sponsored), the field-level output is legitimately correct
    (every slot nulled), but the *engine* cannot tell that apart from a
    post_process pipeline broken by markup drift -- both look like "matched
    N nodes, all with content, all came back None". `_warn_on_silent_nulls`
    already exists for exactly that ambiguity (see its docstring) and applies
    unmodified here; a 100%-sponsored page is worth a human glancing at
    regardless of which explanation is true."""

    def test_every_row_matching_the_pattern_still_warns(self):
        rule = ExtractRule(
            type="css",
            fields={
                "url": FieldRule(
                    selector="a",
                    attr="href",
                    all=True,
                    post_process=[PostProcess(op="null_if_regex", args=[r"^/sspa/click"])],
                ),
            },
        )
        html = (
            '<a href="/sspa/click?url=%2Fdp%2FX">x</a>'
            '<a href="/sspa/click?url=%2Fdp%2FY">y</a>'
        )
        data, warnings = extract_fields(html, rule)
        assert data["url"] == [None, None]
        assert any("post_process" in w and "null_if_regex" in w for w in warnings)


class TestBackwardCompat:
    def test_empty_post_process_behaves_like_before(self):
        rule = ExtractRule(
            type="css",
            fields={"title": FieldRule(selector="h1")},
        )
        data, _ = extract_fields(HTML_TITLE, rule)
        assert data["title"] == "Hello World"
