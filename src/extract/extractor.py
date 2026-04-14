from __future__ import annotations
from typing import Any, Dict, Tuple
from lxml import html as lxml_html

from src.extract.models import ExtractRule


def _text(node) -> str:
    return " ".join((node.text_content() or "").split())


def _outer_html(node) -> str:
    return lxml_html.tostring(node, encoding="unicode", with_tail=False)


def extract_fields(page_html: str, rule: ExtractRule) -> Tuple[Dict[str, Any], list[str]]:
    doc = lxml_html.fromstring(page_html)
    data: Dict[str, Any] = {}
    warnings: list[str] = []

    for name, field_rule in rule.fields.items():
        try:
            nodes = doc.cssselect(field_rule.selector) if rule.type == "css" else doc.xpath(field_rule.selector)
        except Exception:
            warnings.append(f"field '{name}': invalid selector")
            data[name] = [] if field_rule.all else None
            continue

        if not nodes:
            if field_rule.required:
                warnings.append(f"field '{name}': required selector not found")
            data[name] = [] if field_rule.all else None
            continue

        def pick(node, field_rule=field_rule):
            if field_rule.attr == "text":
                return _text(node)
            if field_rule.attr == "html":
                return _outer_html(node)
            return node.get(field_rule.attr)

        if field_rule.all:
            data[name] = [pick(node) for node in nodes]
        else:
            data[name] = pick(nodes[0])

    return data, warnings
