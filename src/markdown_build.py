"""Assemble the markdown-family scrape outputs after render.

Glue between the pure HTML/markdown helpers (src.extract.markdown) and the
async LLM filter (src.presets.llm.markdown_filter). Lives at the worker layer
so src.extract.markdown stays free of LLM/settings coupling. Called from the
worker subprocess once the rendered HTML is in hand.
"""
from __future__ import annotations

import logging

from src.extract.markdown import (
    convert_links_to_citations,
    extract_links,
    html_to_markdown,
    only_main_content,
    prune_html,
)
from src.presets.llm.client import LLMError
from src.presets.llm.markdown_filter import llm_markdown_filter
from src.schemas import MarkdownOptions
from src.settings import settings

log = logging.getLogger(__name__)

# Formats produced by this module (the rest — raw_html, screenshot — are
# handled by the existing worker path).
PRODUCED_FORMATS = frozenset({"markdown", "fit_markdown", "html", "links"})


async def apply(
    request: dict, html: str, *, base_url: str
) -> tuple[dict, list[str]]:
    """Worker bridge: build markdown-family outputs for a serialized job dict.

    Mirrors src.presets.worker_parse.apply — the worker loop calls this once
    after render so the format resolution and option-construction stay in this
    testable layer instead of inline in the worker. Returns ({}, []) when the
    request asks for no markdown-family format (the common legacy case).
    """
    fmts = resolve_formats(
        request.get("formats"),
        raw_html=request.get("raw_html", False),
        screenshot=request.get("screenshot", False),
    )
    if not html or not fmts & PRODUCED_FORMATS:
        return {}, []
    opts = MarkdownOptions(**(request.get("markdown_options") or {}))
    try:
        return await build_markdown_outputs(html, base_url=base_url, fmts=fmts, opts=opts)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Best-effort: the scrape already succeeded, so a markdown conversion
        # bug must degrade to a warning, never fail the whole job.
        log.exception("markdown generation failed for %s", base_url)
        return {}, [f"markdown generation failed: {exc}"]


def resolve_formats(
    formats: list[str] | None, *, raw_html: bool, screenshot: bool
) -> set[str]:
    """Effective output set: union of explicit `formats` and legacy booleans.

    `formats=None` means a legacy caller, so only the booleans count — markdown
    is never produced unless asked for explicitly.
    """
    resolved: set[str] = set(formats or [])
    if raw_html:
        resolved.add("raw_html")
    if screenshot:
        resolved.add("screenshot")
    return resolved


async def build_markdown_outputs(
    html: str,
    *,
    base_url: str,
    fmts: set[str],
    opts: MarkdownOptions,
) -> tuple[dict, list[str]]:
    """Build requested markdown-family outputs from rendered `html`.

    Returns (outputs, warnings). Never raises on filter failure — it degrades
    and records a warning, because the scrape itself already succeeded.
    """
    outputs: dict = {}
    warnings: list[str] = []
    main_html = only_main_content(html) if opts.only_main_content else html

    if "markdown" in fmts:
        markdown = html_to_markdown(
            main_html,
            base_url=base_url,
            ignore_links=opts.ignore_links,
            ignore_images=opts.ignore_images,
            body_width=opts.body_width,
        )
        if opts.citations:
            markdown, references = convert_links_to_citations(markdown, base_url)
            outputs["markdown_references"] = references
        outputs["markdown"] = markdown

    if "fit_markdown" in fmts:
        outputs["fit_markdown"] = await _build_fit_markdown(
            html, base_url=base_url, opts=opts, warnings=warnings
        )

    if "html" in fmts:
        outputs["html"] = main_html

    if "links" in fmts:
        outputs["links"] = extract_links(html, base_url)

    return outputs, warnings


async def _build_fit_markdown(
    html: str, *, base_url: str, opts: MarkdownOptions, warnings: list[str]
) -> str:
    content_filter = opts.content_filter
    if content_filter == "none":
        content_filter = "pruning"
        warnings.append("fit_markdown requested without content_filter; using pruning")

    if content_filter == "llm":
        try:
            return await llm_markdown_filter(
                html,
                model=opts.filter_model or settings.default_llm_model,
                instruction=opts.filter_instruction,
            )
        except LLMError as exc:
            log.warning(
                "llm content_filter failed for %s (%s); falling back to pruning",
                base_url,
                exc,
            )
            warnings.append(f"llm content_filter failed ({exc}); fell back to pruning")

    return html_to_markdown(
        prune_html(html),
        base_url=base_url,
        ignore_links=opts.ignore_links,
        ignore_images=opts.ignore_images,
        body_width=opts.body_width,
    )
