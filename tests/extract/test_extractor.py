from __future__ import annotations

import pytest

from src.extract.extractor import extract_fields, _text, _outer_html
from src.extract.models import ExtractRule, FieldRule


class TestHelperFunctions:
    def test_text_function(self):
        """Function _text make normalizing spaces"""
        from lxml import html as lxml_html

        html = "<div>  Hello   World  </div>"
        element = lxml_html.fromstring(html)

        result = _text(element)
        assert result == "Hello World"

    def test_text_function_multiline(self):
        """Function _text processed multiline text"""
        from lxml import html as lxml_html

        html = "<div>\n  Line 1\n  Line 2\n</div>"
        element = lxml_html.fromstring(html)

        result = _text(element)
        assert "Line 1" in result
        assert "Line 2" in result

    def test_outer_html_function(self):
        """Function _outer_html return HTML"""
        from lxml import html as lxml_html

        html = "<div class='test'>Content</div>"
        element = lxml_html.fromstring(html)

        result = _outer_html(element)
        assert "<div" in result
        assert "class" in result
        assert "Content" in result


class TestCSSExtraction:
    def test_extract_text_single(self, sample_html):
        """Extract text from single element"""
        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1", attr="text"),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert data["title"] == "Main Title"
        assert warnings == []

    def test_extract_text_multiple_all_false(self, sample_html):
        """Extract first element with all=False"""
        rule = ExtractRule(
            type="css",
            fields={
                "text": FieldRule(selector="p.text", attr="text", all=False),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert data["text"] == "First paragraph"
        assert warnings == []

    def test_extract_text_multiple_all_true(self, sample_html):
        """Extract all elements with all=True"""
        rule = ExtractRule(
            type="css",
            fields={
                "texts": FieldRule(selector="p.text", attr="text", all=True),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert len(data["texts"]) == 2
        assert "First paragraph" in data["texts"]
        assert "Second paragraph" in data["texts"]
        assert warnings == []

    def test_extract_html(self, sample_html):
        """Extract HTML"""
        rule = ExtractRule(
            type="css",
            fields={
                "content": FieldRule(selector="div.content", attr="html"),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert "<div" in data["content"]
        assert "content" in data["content"]
        assert warnings == []

    def test_extract_attribute_href(self, sample_html):
        """Extract attribute href"""
        rule = ExtractRule(
            type="css",
            fields={
                "link": FieldRule(selector="a.link", attr="href"),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert data["link"] == "https://example.com"
        assert warnings == []

    def test_extract_attribute_class(self, sample_html):
        """Extract attribute class"""
        rule = ExtractRule(
            type="css",
            fields={
                "class": FieldRule(selector="a.link", attr="class"),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert data["class"] == "link"
        assert warnings == []

    def test_extract_attribute_custom(self, sample_html):
        """Extract custom attribute"""
        rule = ExtractRule(
            type="css",
            fields={
                "ids": FieldRule(selector=".item", attr="data-id", all=True),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert len(data["ids"]) == 3
        assert "1" in data["ids"]
        assert "2" in data["ids"]
        assert "3" in data["ids"]
        assert warnings == []


class TestXPathExtraction:
    def test_extract_xpath_text(self, sample_html):
        """Extract text in XPath - use elements, not/text()"""
        rule = ExtractRule(
            type="xpath",
            fields={
                "title": FieldRule(selector="//h1", attr="text"),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert "Main Title" in data["title"]
        assert warnings == []

    def test_extract_xpath_attribute(self, sample_html):
        """Extract attributes in XPath - use elements, not/@attr"""
        rule = ExtractRule(
            type="xpath",
            fields={
                "link": FieldRule(selector="//a[@class='link']", attr="href"),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert data["link"] == "https://example.com"
        assert warnings == []

    def test_extract_xpath_multiple(self, sample_html):
        """Extract a few elements in XPath"""
        rule = ExtractRule(
            type="xpath",
            fields={
                "items": FieldRule(selector="//div[@class='item']", attr="text", all=True),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert len(data["items"]) == 3
        assert warnings == []


class TestErrorHandling:
    def test_extract_selector_not_found_optional(self, sample_html):
        """Not required selector not found"""
        rule = ExtractRule(
            type="css",
            fields={
                "missing": FieldRule(selector=".not-exists", required=False),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert data["missing"] is None
        assert warnings == []

    def test_extract_selector_not_found_required(self, sample_html):
        """Required selector not found"""
        rule = ExtractRule(
            type="css",
            fields={
                "missing": FieldRule(selector=".not-exists", required=True),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert data["missing"] is None
        assert len(warnings) == 1
        assert "required selector not found" in warnings[0]

    def test_extract_invalid_selector(self, sample_html):
        """Not valid selector"""
        rule = ExtractRule(
            type="css",
            fields={
                "invalid": FieldRule(selector="[[[invalid", required=False, all=False),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        # If selector error, than code will return None (if all=False)
        assert data["invalid"] is None
        assert len(warnings) == 1
        assert "invalid selector" in warnings[0]

    def test_extract_invalid_selector_with_all(self, sample_html):
        """Not valid selector withall=True"""
        rule = ExtractRule(
            type="css",
            fields={
                "invalid": FieldRule(selector="[[[invalid", required=False, all=True),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        # If selector error, code will return [] (if all=True)
        assert data["invalid"] == []
        assert len(warnings) == 1
        assert "invalid selector" in warnings[0]

    def test_extract_empty_html(self):
        """Empty HTML - it call parsing error in lxml"""
        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1"),
            },
        )

        # Empty HTML call ParserError in lxml
        with pytest.raises(Exception):
            data, warnings = extract_fields("", rule)

    def test_extract_minimal_html(self):
        """Minial valid HTML"""
        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1"),
            },
        )

        # Minimal HTML without h1
        data, warnings = extract_fields("<html><body></body></html>", rule)

        assert data["title"] is None


class TestComplexScenarios:
    def test_extract_multiple_fields(self, sample_html):
        """Extract a few fields at the same time"""
        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1", attr="text"),
                "paragraphs": FieldRule(selector="p.text", attr="text", all=True),
                "link": FieldRule(selector="a.link", attr="href"),
                "items": FieldRule(selector=".item", attr="data-id", all=True),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert data["title"] == "Main Title"
        assert len(data["paragraphs"]) == 2
        assert data["link"] == "https://example.com"
        assert len(data["items"]) == 3
        # paragraphs (2) and items (3) are two unrelated all=true groups that
        # happen to differ in length — exactly the accepted-noise case the
        # row-alignment guard is designed to flag (see TestRowAlignmentGuard).
        assert len(warnings) == 1
        assert "row_alignment_mismatch" in warnings[0]

    def test_extract_with_warnings(self, sample_html):
        """Extract with warnings"""
        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1"),
                "missing": FieldRule(selector=".not-found", required=True),
                "invalid": FieldRule(selector="[[["),
            },
        )

        data, warnings = extract_fields(sample_html, rule)

        assert data["title"] == "Main Title"
        assert data["missing"] is None
        assert len(warnings) >= 2

    def test_extract_text_normalization(self):
        """Text Normalization with extra spaces"""
        html = """
        <html>
            <body>
                <h1>  Title   with   spaces  </h1>
            </body>
        </html>
        """

        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1", attr="text"),
            },
        )

        data, warnings = extract_fields(html, rule)

        assert data["title"] == "Title with spaces"
        assert warnings == []


class TestRowAlignmentGuard:
    """Consumers zip `all=true` fields by index (prices[i] belongs to
    titles[i]), but the engine extracts every field independently and never
    checked they came out the same length. This silently misaligned three
    shipped presets (amazon_search price/rating drift, amazon.co.uk empty
    urls list, google_search doubled snippets) — these tests pin the guard
    that turns that class of bug into a loud warning.

    Known false negative: this is a LENGTH check, not an alignment check.
    Compensating errors within a single field can cancel out — e.g. one
    card missing a rating while another card renders an extra one — leaving
    `titles=3, ratings=3` with no warning even though the rating for
    product A is actually product B's. That is exactly the two known
    amazon_search shapes (a card with no rating; a card with extra nodes)
    co-occurring on one page. A clean `warnings == []` is not proof the
    rows are aligned."""

    def test_mismatched_all_field_lengths_emit_one_warning(self):
        html = """
        <html><body>
            <span class="title">A</span>
            <span class="title">B</span>
            <span class="title">C</span>
            <span class="price">10</span>
            <span class="price">20</span>
        </body></html>
        """
        rule = ExtractRule(
            type="css",
            fields={
                "titles": FieldRule(selector=".title", all=True),
                "prices": FieldRule(selector=".price", all=True),
            },
        )

        data, warnings = extract_fields(html, rule)

        assert data["titles"] == ["A", "B", "C"]
        assert data["prices"] == ["10", "20"]
        alignment_warnings = [w for w in warnings if "titles" in w and "prices" in w]
        assert len(alignment_warnings) == 1
        assert "'titles'=3" in alignment_warnings[0]
        assert "'prices'=2" in alignment_warnings[0]

    def test_equal_all_field_lengths_no_warning(self):
        html = """
        <html><body>
            <span class="title">A</span>
            <span class="title">B</span>
            <span class="price">10</span>
            <span class="price">20</span>
        </body></html>
        """
        rule = ExtractRule(
            type="css",
            fields={
                "titles": FieldRule(selector=".title", all=True),
                "prices": FieldRule(selector=".price", all=True),
            },
        )

        _, warnings = extract_fields(html, rule)

        assert warnings == []

    def test_single_all_field_has_nothing_to_compare(self):
        """Only one `all=true` field in the rule -> no pairing to check."""
        html = """
        <html><body>
            <span class="title">A</span>
            <span class="title">B</span>
        </body></html>
        """
        rule = ExtractRule(
            type="css",
            fields={
                "titles": FieldRule(selector=".title", all=True),
            },
        )

        _, warnings = extract_fields(html, rule)

        assert warnings == []

    def test_scalar_fields_are_ignored_by_the_guard(self):
        """all=false fields only ever return the first match, so their
        underlying selector cardinality (3 vs 1 nodes here) is irrelevant —
        and must not be conflated with a real all=true mismatch."""
        html = """
        <html><body>
            <p class="text">First</p>
            <p class="text">Second</p>
            <p class="text">Third</p>
            <a class="link" href="https://example.com">Link</a>
            <span class="title">A</span>
            <span class="title">B</span>
        </body></html>
        """
        rule = ExtractRule(
            type="css",
            fields={
                "text": FieldRule(selector="p.text", attr="text", all=False),
                "link": FieldRule(selector="a.link", attr="href", all=False),
                "titles": FieldRule(selector=".title", all=True),
            },
        )

        _, warnings = extract_fields(html, rule)

        assert warnings == []

    def test_empty_all_field_alongside_populated_one_warns(self):
        """Mirrors the amazon.co.uk bug: urls=[] next to a fully populated
        titles list on a page layout variant. Empty must count, not be
        treated as 'nothing matched, nothing to warn about'."""
        html = """
        <html><body>
            <span class="title">A</span>
            <span class="title">B</span>
        </body></html>
        """
        rule = ExtractRule(
            type="css",
            fields={
                "titles": FieldRule(selector=".title", all=True),
                "urls": FieldRule(selector=".missing-url", attr="href", all=True),
            },
        )

        data, warnings = extract_fields(html, rule)

        assert data["titles"] == ["A", "B"]
        assert data["urls"] == []
        alignment_warnings = [w for w in warnings if "titles" in w and "urls" in w]
        assert len(alignment_warnings) == 1

    def test_four_fields_three_agree_one_drifts_names_every_field(self):
        """Mirrors the real shipped amazon_search shape (titles=22, urls=22,
        prices=36, ratings=22): three of four `all=true` fields agree and
        one drifts. The message must name every `all=true` field with its
        length -- including the three that agree, not just the outlier --
        which no 2-field test above can pin."""
        titles_urls_ratings = "".join(
            f'<span class="title">T{i}</span>'
            f'<span class="url">https://example.com/{i}</span>'
            f'<span class="rating">{i % 5}</span>'
            for i in range(22)
        )
        extra_prices = "".join(f'<span class="price">{i}</span>' for i in range(36))
        html = f"<html><body>{titles_urls_ratings}{extra_prices}</body></html>"

        rule = ExtractRule(
            type="css",
            fields={
                "titles": FieldRule(selector=".title", all=True),
                "urls": FieldRule(selector=".url", all=True),
                "prices": FieldRule(selector=".price", all=True),
                "ratings": FieldRule(selector=".rating", all=True),
            },
        )

        data, warnings = extract_fields(html, rule)

        assert len(data["titles"]) == 22
        assert len(data["urls"]) == 22
        assert len(data["prices"]) == 36
        assert len(data["ratings"]) == 22
        alignment_warnings = [w for w in warnings if "row_alignment_mismatch" in w]
        assert len(alignment_warnings) == 1
        message = alignment_warnings[0]
        assert "'titles'=22" in message
        assert "'urls'=22" in message
        assert "'prices'=36" in message
        assert "'ratings'=22" in message

    def test_guard_does_not_alter_returned_data(self):
        """The guard only appends a warning; it must never reshape, pad, or
        truncate the `data` it is inspecting."""
        html = """
        <html><body>
            <span class="title">A</span>
            <span class="title">B</span>
            <span class="title">C</span>
            <span class="price">10</span>
        </body></html>
        """
        rule = ExtractRule(
            type="css",
            fields={
                "titles": FieldRule(selector=".title", all=True),
                "prices": FieldRule(selector=".price", all=True),
            },
        )

        data, warnings = extract_fields(html, rule)

        assert data == {"titles": ["A", "B", "C"], "prices": ["10"]}
        assert len(warnings) == 1
