"""Shrink a rendered page before sending it to an LLM.

Drops non-content nodes (script/style/svg/noscript/iframe/template),
collapses runs of whitespace, and hard-truncates to a character budget so a
huge page can't blow the context window or the bill. This is lossy on
purpose — selector generation and extraction only need the visible DOM
structure, not inline JS or vector art.
"""
from __future__ import annotations

import re

from lxml import etree, html as lxml_html

_DROP_TAGS = ("script", "style", "svg", "noscript", "iframe", "template")
_WS = re.compile(r"\s+")
DEFAULT_MAX_CHARS = 60_000


def clean_html(page_html: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    try:
        doc = lxml_html.fromstring(page_html)
    except (etree.ParserError, ValueError):  # pylint: disable=c-extension-no-member
        # Unparseable — fall back to whitespace-collapsed truncation.
        return _WS.sub(" ", page_html).strip()[:max_chars]

    for tag in _DROP_TAGS:
        for node in doc.iter(tag):
            node.drop_tree()

    serialized = lxml_html.tostring(doc, encoding="unicode")
    collapsed = _WS.sub(" ", serialized).strip()
    # Hard slice can split a tag/entity at the boundary; acceptable because
    # the LLM only needs DOM structure and tolerates a broken tail.
    return collapsed[:max_chars]
