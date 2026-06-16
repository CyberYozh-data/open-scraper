"""LLM-based "fit markdown" filter.

The deterministic prune_html heuristic is cheap but blunt; this is the higher
quality path — the model reads the page and returns clean, content-only
markdown. Mirrors Crawl4AI's LLMContentFilter: the model is told to wrap its
answer in <content>…</content> so we can lift the markdown out cleanly.

Reuses the shared async LLM client (src.presets.llm.client) and the HTML
shrinker (htmlclean) so a huge page can't blow the context window.
"""
from __future__ import annotations

import re

from src.presets.llm.client import complete
from src.presets.llm.htmlclean import clean_html

_CONTENT_TAG = re.compile(r"<content>(.*?)</content>", re.DOTALL | re.IGNORECASE)

_DEFAULT_INSTRUCTION = (
    "Convert this HTML into clean, content-only Markdown. Remove navigation, "
    "ads, cookie banners, sidebars, and footers. Keep headings, paragraphs, "
    "lists, tables, and code."
)

_SYSTEM = (
    "You turn raw HTML into clean, LLM-ready Markdown. Use proper Markdown "
    "(#, **, lists, fenced code, Markdown tables). Wrap your entire answer in "
    "<content>…</content> tags and output nothing else."
)


async def llm_markdown_filter(
    html: str,
    *,
    model: str,
    instruction: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """Return content-only Markdown produced by the model.

    Falls back to the raw response (stripped) if the model omits the
    <content> wrapper. Raises LLMError on call failure — the caller decides
    whether to degrade to prune-based fit_markdown.
    """
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"{instruction or _DEFAULT_INSTRUCTION}\n\n"
                f"HTML:\n{clean_html(html)}"
            ),
        },
    ]
    raw = await complete(model, messages, max_tokens=max_tokens)
    match = _CONTENT_TAG.search(raw)
    return (match.group(1) if match else raw).strip()
