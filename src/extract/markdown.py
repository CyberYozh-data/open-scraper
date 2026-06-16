"""Convert rendered HTML into LLM-ready Markdown.

Mirrors the approach the market leaders take (Crawl4AI / Firecrawl): a tuned
html2text pass for the conversion plus optional noise filtering applied to the
*HTML* before conversion (never to the markdown string). Pure, synchronous
helpers — the worker calls them after render; the LLM-based filter lives in
src/presets/llm/markdown_filter.py because it needs an async client.
"""
from __future__ import annotations

import math
import re
from urllib.parse import urljoin

import html2text
from lxml import etree, html as lxml_html

# Markdown inline link: [text](url "optional title"). Captures text + url.
# The negative lookbehind skips image syntax ![alt](url) so a page's images
# don't turn into bogus citations.
_INLINE_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

# Boilerplate selectors removed when only_main_content is requested. Ported
# from Firecrawl's excludeNonMainTags list, minus its hardcoded `.swoogo-*`
# carve-out (a single-customer special case). Default-off in our API because
# yozh-law-checker specifically audits footer/cookie/nav elements.
_EXCLUDE_NON_MAIN = [
    "header", "footer", "nav", "aside",
    ".header", ".top", ".navbar", "#header",
    ".footer", ".bottom", "#footer",
    ".sidebar", ".side", ".aside", "#sidebar",
    ".modal", ".popup", "#modal", ".overlay",
    ".ad", ".ads", ".advert", "#ad",
    ".lang-selector", ".language", "#language-selector",
    ".social", ".social-media", ".social-links", "#social",
    ".menu", ".navigation", "#nav",
    ".breadcrumbs", "#breadcrumbs",
    ".share", "#share", ".widget", "#widget",
    ".cookie", "#cookie",
]


def html_to_markdown(
    html: str,
    *,
    base_url: str = "",
    ignore_links: bool = False,
    ignore_images: bool = False,
    body_width: int = 0,
) -> str:
    """Render `html` to Markdown via a tuned html2text converter.

    body_width=0 disables hard line wrapping (long paragraphs stay on one
    line, which LLMs prefer). mark_code=True emits fenced code blocks.
    """
    converter = html2text.HTML2Text(baseurl=base_url)
    converter.body_width = body_width
    converter.ignore_links = ignore_links
    converter.ignore_images = ignore_images
    converter.mark_code = True
    markdown = converter.handle(html)
    return _fence_code_blocks(markdown).strip()


def _fence_code_blocks(markdown: str) -> str:
    """Turn html2text `[code]`/`[/code]` markers into ``` fenced blocks.

    With mark_code=True html2text wraps code in `[code]`/`[/code]` and indents
    the body four spaces. LLMs read fenced blocks better, so we swap the
    markers for ``` fences and drop the one level of indentation it added.
    """
    out: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped == "[code]":
            in_code = True
            out.append("```")
        elif stripped == "[/code]":
            in_code = False
            out.append("```")
        elif in_code and line.startswith("    "):
            out.append(line[4:])
        else:
            out.append(line)
    return "\n".join(out)


def only_main_content(html: str) -> str:
    """Strip navigation/boilerplate, returning the cleaned HTML.

    Selector-based (like Firecrawl), not Readability — cheap and predictable.
    Returns the input unchanged when it is empty or unparseable so the caller
    can convert whatever it has rather than failing the scrape.
    """
    doc = _parse_or_none(html)
    if doc is None:
        return html
    for selector in _EXCLUDE_NON_MAIN:
        for node in doc.cssselect(selector):
            node.drop_tree()
    return lxml_html.tostring(doc, encoding="unicode")


# Per-tag structural weight (component of the prune score). Content-bearing
# tags score high; generic containers low. Ported from Crawl4AI's
# PruningContentFilter tag weights.
_TAG_WEIGHTS = {
    "article": 1.5, "main": 1.4, "section": 1.0, "p": 1.0,
    "h1": 1.2, "h2": 1.1, "h3": 1.0, "h4": 0.9, "h5": 0.8, "h6": 0.7,
    "ul": 0.8, "ol": 0.8, "table": 0.8, "blockquote": 0.9,
    "div": 0.5, "span": 0.4,
    "nav": 0.2, "aside": 0.3, "header": 0.3, "footer": 0.2, "form": 0.3,
}
# Tags evaluated for pruning. Leaf inline/content tags are left to their
# container's verdict so we never strip the words out of a kept block.
_PRUNE_TAGS = (
    "div", "section", "article", "main", "aside", "nav",
    "header", "footer", "form", "ul", "ol", "table",
)
_NEGATIVE_CLASSID = re.compile(
    r"nav|menu|footer|header|sidebar|side|ad|advert|social|share|widget|"
    r"breadcrumb|cookie|popup|modal|banner|promo",
    re.IGNORECASE,
)


def _node_score(node) -> float:
    """Composite relevance score in roughly [0, 1.5]; higher = more content."""
    inner_html = lxml_html.tostring(node, encoding="unicode")
    text = node.text_content()
    text_len = len(text.strip())
    link_text_len = sum(len((a.text_content() or "").strip()) for a in node.iter("a"))

    text_density = text_len / max(len(inner_html), 1)
    link_density = link_text_len / max(text_len, 1)
    link_score = 1.0 - min(link_density, 1.0)
    tag_weight = _TAG_WEIGHTS.get(node.tag, 0.5)
    classid = " ".join(filter(None, (node.get("class"), node.get("id"))))
    classid_score = 0.0 if _NEGATIVE_CLASSID.search(classid) else 1.0
    length_score = min(math.log(text_len + 1) / math.log(1000), 1.0)

    return (
        0.4 * text_density
        + 0.2 * link_score
        + 0.2 * tag_weight
        + 0.1 * classid_score
        + 0.1 * length_score
    )


def prune_html(html: str, *, threshold: float = 0.48) -> str:
    """Drop low-relevance blocks (nav/boilerplate) by heuristic scoring.

    Cheap, LLM-free way to produce a "fit" HTML fragment that then converts to
    fit_markdown. Returns the input unchanged when empty or unparseable.
    """
    doc = _parse_or_none(html)
    if doc is None:
        return html
    doomed = [node for node in doc.iter(*_PRUNE_TAGS) if _node_score(node) < threshold]
    for node in doomed:
        # A parent may have already been dropped, detaching this node.
        if node.getparent() is not None:
            node.drop_tree()
    return lxml_html.tostring(doc, encoding="unicode")


def _parse_or_none(html: str):
    """Parse `html`, or return None when it is empty or unparseable.

    Centralises the parse-or-degrade guard shared by the HTML-mutating helpers
    so each caller picks its own fallback value instead of repeating the
    try/except.
    """
    if not html.strip():
        return None
    try:
        return lxml_html.fromstring(html)
    except (etree.ParserError, ValueError):  # pylint: disable=c-extension-no-member
        return None


def extract_links(html: str, base_url: str = "") -> list[str]:
    """Return absolute hyperlinks in document order, de-duplicated.

    Empty or unparseable input yields an empty list.
    """
    doc = _parse_or_none(html)
    if doc is None:
        return []
    seen: dict[str, None] = {}
    for anchor in doc.iter("a"):
        href = anchor.get("href")
        if not href:
            continue
        url = urljoin(base_url, href) if base_url else href
        # Only surface crawlable web links; drop javascript:/mailto:/data: etc.
        if not url.lower().startswith(("http://", "https://")):
            continue
        seen.setdefault(url, None)
    return list(seen)


def convert_links_to_citations(markdown: str, base_url: str = "") -> tuple[str, str]:
    """Replace inline links with numbered ⟨n⟩ markers + a references list.

    Returns (body_with_markers, references_block). Repeated URLs share a
    number; relative URLs are resolved against base_url. References use the
    Unicode angle brackets ⟨ ⟩ (matching Crawl4AI) so the markers never
    collide with literal `<`/`>` in the text.
    """
    numbers: dict[str, int] = {}

    def _replace(match: re.Match) -> str:
        text, href = match.group(1), match.group(2)
        url = urljoin(base_url, href) if base_url else href
        number = numbers.get(url)
        if number is None:
            number = len(numbers) + 1
            numbers[url] = number
        return f"{text}⟨{number}⟩"

    body = _INLINE_LINK.sub(_replace, markdown)
    references = "\n".join(
        f"⟨{number}⟩ {url}" for url, number in numbers.items()
    )
    return body, references
