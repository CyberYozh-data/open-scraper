"""Web search endpoint: SERP + optional scrape in one call.

Reuses the built-in `google_search` preset as the SERP source — materialize it
with the query, run it through the normal job queue, parse the organic result
blocks, and (optionally) scrape each result page with the same pipeline. The
search logic is factored into `build_search` with an injected `run_job` so it
is unit-testable without the worker pool.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException
from lxml import etree, html as lxml_html

from src.api.presets import get_preset_store
from src.api.session_guard import validate_session_id
from src.presets.materializer import (
    MaterializeError,
    PresetScrapeRequest,
    materialize,
)
from src.presets.store import PresetNotFound, PresetStore
from src.schemas import (
    ScrapeRequest,
    ScrapeResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from src.scrape_service import scrape_service

log = logging.getLogger(__name__)
router = APIRouter()

@dataclass(frozen=True)
class EngineProfile:
    """How to reach and parse one SERP engine.

    `preset` supplies the URL template, locales and the `result_blocks` selector
    (one HTML fragment per organic result). `title_sel`/`snippet_sel` are CSS
    selectors applied *within* each block, so they are deliberately block-relative
    (unscoped) — they assume each `result_blocks` fragment is exactly one result.
    The result link is the first http(s) anchor in the block (shared heuristic).
    """

    preset: str
    title_sel: str
    snippet_sel: str


# One entry per engine — adding a fourth is a new preset + one line here.
ENGINES: dict[str, EngineProfile] = {
    "google": EngineProfile("google_search", "h3", "div[data-sncf='1'], .VwiC3b"),
    "bing": EngineProfile("bing_search", "h2", ".b_caption p, p.b_lineclamp2"),
    "yandex": EngineProfile(
        "yandex_search", ".OrganicTitle-LinkText, h2", ".OrganicText, .TextContainer"
    ),
}

RunJob = Callable[[list[ScrapeRequest]], Awaitable[list[ScrapeResponse]]]


def _first_http_link(el) -> str | None:
    for anchor in el.iter("a"):
        href = (anchor.get("href") or "").strip()
        if href.startswith(("http://", "https://")):
            return href
    return None


def _unwrap_redirect(url: str) -> str:
    """Return the real destination behind an engine's tracking redirect.

    Bing wraps every organic link in a ``/ck/a`` redirect with the destination
    base64url-encoded in the ``u=a1<…>`` param. Decode it so callers get the
    target site, not the tracker. Non-Bing or unrecognised URLs pass through.

    The input is SERP HTML Bing itself returned (not caller-supplied), and we
    only ever return an ``http(s)`` string, so a crafted ``u=`` can't yield a
    ``javascript:`` / ``file:`` link — worst case is a passthrough.
    """
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host != "bing.com" and not host.endswith(".bing.com"):
            return url
        if "/ck/a" not in parsed.path:
            return url
        u = parse_qs(parsed.query).get("u", [""])[0]
        if not u.startswith("a1"):
            return url
        # Over-pad with '='; the decoder ignores any excess padding.
        decoded = base64.urlsafe_b64decode(u[2:] + "===").decode("utf-8", "ignore")
        return decoded if decoded.startswith(("http://", "https://")) else url
    except ValueError as exc:
        # The URL failed to parse, or a u=a1… payload failed to decode —
        # i.e. Bing changed its encoding; worth a server-side signal.
        log.warning("failed to unwrap bing redirect url=%r: %s", url, exc)
        return url


def _parse_results(data: dict[str, Any], profile: EngineProfile) -> list[SearchResult]:
    """Turn the preset's per-result HTML blocks into clean (url, title, snippet).

    We parse `result_blocks` (one HTML fragment per organic result) rather than
    the preset's flat `titles`/`links`/`snippets` arrays: the link selector
    matches several anchors per result, so those arrays are misaligned and can't
    be zipped reliably. Parsing per block keeps title/url/snippet together and
    lets us drop non-organic blocks (no title element) and duplicate URLs.
    """
    out: list[SearchResult] = []
    seen: set[str] = set()
    for block_html in data.get("result_blocks") or []:
        if not block_html:
            continue
        try:
            el = lxml_html.fromstring(block_html)
        except (ValueError, etree.ParserError):  # pylint: disable=c-extension-no-member
            continue
        title_els = el.cssselect(profile.title_sel)
        if not title_els:
            continue
        url = _first_http_link(el)
        if not url:
            continue
        url = _unwrap_redirect(url)
        if url in seen:
            continue
        seen.add(url)
        snippet_els = el.cssselect(profile.snippet_sel)
        out.append(
            SearchResult(
                url=url,
                title=title_els[0].text_content().strip() or None,
                snippet=snippet_els[0].text_content().strip() if snippet_els else None,
            )
        )
    return out


def _proxy_override(req: SearchRequest) -> dict[str, Any]:
    """Proxy fields the caller set, as a ScrapeRequest-shaped dict.

    Only includes fields that were explicitly provided, so an unset proxy
    leaves the SERP preset's own proxy untouched (default-off).
    """
    override: dict[str, Any] = {}
    if req.proxy_type is not None:
        override["proxy_type"] = req.proxy_type
    if req.proxy_pool_id is not None:
        override["proxy_pool_id"] = req.proxy_pool_id
    if req.proxy_geo is not None:
        geo = req.proxy_geo.model_dump(exclude_none=True)
        # An all-empty geo is treated as "not provided" so the materializer's
        # locale-derived default (country_code) survives rather than being
        # wiped to {}.
        if geo:
            override["proxy_geo"] = geo
    return override


async def build_search(
    req: SearchRequest, *, store: PresetStore, run_job: RunJob
) -> SearchResponse:
    """Run the SERP preset, parse results, optionally scrape each page."""
    started = time.perf_counter()
    profile = ENGINES[req.engine]
    preset = store.get(profile.preset)
    proxy_override = _proxy_override(req)
    serp_req = materialize(
        preset,
        PresetScrapeRequest(
            source=profile.preset,
            preset_params={"query": req.query},
            locale=req.locale,
            request_override=proxy_override or None,
        ),
    )
    serp_results = await run_job([serp_req])
    serp = serp_results[0] if serp_results else None
    warnings = list(serp.warnings) if serp and serp.warnings else []
    if serp is None:
        # Distinguish "the SERP job didn't complete" from "0 organic results".
        warnings.append("serp_unavailable: search job did not complete")
        log.warning("search serp unavailable query=%r", req.query)
    results = _parse_results((serp.data if serp else None) or {}, profile)[: req.limit]
    log.info("search engine=%s query=%r results=%d", req.engine, req.query, len(results))

    if req.scrape and results:
        await _scrape_result_pages(req, results, proxy_override, run_job, warnings)

    return SearchResponse(
        query=req.query,
        count=len(results),
        results=results,
        took_ms=int((time.perf_counter() - started) * 1000),
        warnings=warnings,
    )


async def _scrape_result_pages(
    req: SearchRequest,
    results: list[SearchResult],
    proxy_override: dict[str, Any],
    run_job: RunJob,
    warnings: list[str],
) -> None:
    """Scrape each SERP result page and attach the responses in place."""
    # Strip internal-only fields a caller must not steer via scrape_options.
    opts = {
        k: v
        for k, v in (req.scrape_options or {}).items()
        if k not in ("preset_meta", "parser_plan")
    }
    # The panel's proxy applies to the result scrapes too, taking precedence
    # over any proxy passed inside scrape_options.
    scrape_reqs = [
        ScrapeRequest(**{**opts, **proxy_override, "url": r.url}) for r in results
    ]
    # Session is validated here (after the SERP) because it is checked
    # against the materialized per-result request, which only exists now.
    for scrape_req in scrape_reqs:
        await validate_session_id(scrape_req)
    responses = await run_job(scrape_reqs)
    for result, response in zip(results, responses):
        result.scrape = response
    # Count what actually got attached: a mid-job timeout yields a full-length
    # list padded with None placeholders, not a short one (and the record's
    # list keeps being mutated by the worker, so don't recount `responses`).
    scraped = sum(1 for r in results if r.scrape is not None)
    if scraped < len(results):
        warnings.append(f"scrape_incomplete: {scraped} of {len(results)} pages scraped")
        log.warning(
            "search scrape incomplete query=%r scraped=%d of %d",
            req.query, scraped, len(results),
        )


@router.post("", response_model=SearchResponse, operation_id="run_search")
async def run_search(
    req: SearchRequest, store: PresetStore = Depends(get_preset_store)
) -> SearchResponse:
    try:
        return await build_search(req, store=store, run_job=scrape_service.run_and_wait)
    except PresetNotFound as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{ENGINES[req.engine].preset} preset is missing",
        ) from exc
    except MaterializeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
