from __future__ import annotations

from src.extract.models import FieldRule, ExtractRule


class TestFieldRule:
    def test_field_rule_defaults(self):
        """Defaults for FieldRule"""
        rule = FieldRule(selector="h1")

        assert rule.selector == "h1"
        assert rule.attr == "text"
        assert rule.all is False
        assert rule.required is False

    def test_field_rule_custom_attr(self):
        """FieldRule with custom attribute"""
        rule = FieldRule(selector="a", attr="href")

        assert rule.selector == "a"
        assert rule.attr == "href"

    def test_field_rule_all_flag(self):
        """FieldRule with set all"""
        rule = FieldRule(selector=".item", all=True)

        assert rule.all is True

    def test_field_rule_required_flag(self):
        """FieldRule with set required"""
        rule = FieldRule(selector=".required", required=True)

        assert rule.required is True

    def test_field_rule_html_attr(self):
        """FieldRule with attr html"""
        rule = FieldRule(selector="div.content", attr="html")

        assert rule.attr == "html"

    def test_field_rule_full(self):
        """Full FieldRule"""
        rule = FieldRule(
            selector=".items > .item",
            attr="data-id",
            all=True,
            required=True,
        )

        assert rule.selector == ".items > .item"
        assert rule.attr == "data-id"
        assert rule.all is True
        assert rule.required is True


class TestExtractRule:
    def test_extract_rule_css(self):
        """ExtractRule with CSS"""
        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1"),
            },
        )

        assert rule.type == "css"
        assert "title" in rule.fields

    def test_extract_rule_xpath(self):
        """ExtractRule with XPath"""
        rule = ExtractRule(
            type="xpath",
            fields={
                "title": FieldRule(selector="//h1/text()"),
            },
        )

        assert rule.type == "xpath"

    def test_extract_rule_multiple_fields(self):
        """ExtractRule with a few fields"""
        rule = ExtractRule(
            type="css",
            fields={
                "title": FieldRule(selector="h1"),
                "description": FieldRule(selector="p.desc"),
                "links": FieldRule(selector="a", attr="href", all=True),
            },
        )

        assert len(rule.fields) == 3
        assert "title" in rule.fields
        assert "description" in rule.fields
        assert "links" in rule.fields

    def test_extract_rule_validation_css(self):
        """Validation CSS type"""
        rule = ExtractRule(type="css", fields={})
        assert rule.type == "css"

    def test_extract_rule_validation_xpath(self):
        """Validation XPath type"""
        rule = ExtractRule(type="xpath", fields={})
        assert rule.type == "xpath"
