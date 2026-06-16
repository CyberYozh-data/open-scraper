from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

import httpx
from fastapi import APIRouter, Request as FastapiRequest

from ..dedup import DedupSet, canonicalize_url
from ..linkextract import extract_links
from ..schemas import MapRequest, MapResponse, MapStats
from ..scope import CompositeScope
from ..settings import settings
from ..sitemap import collect_sitemap_urls, discover_sitemap_urls
from ..ssrf import safe_get


log = logging.getLogger(__name__)
router = APIRouter()

# (url, scrape_options) -> ScrapeResponse dict
ScraperFetch = Callable[[str, dict], Awaitable[dict]]


def _proxy_scrape_options(req: MapRequest) -> dict:
    """Proxy fields to forward to the scraper on the render path (empty for none)."""
    if not req.proxy_type or req.proxy_type == "none":
        return {}
    opts: dict = {"proxy_type": req.proxy_type}
    if req.proxy_pool_id:
        opts["proxy_pool_id"] = req.proxy_pool_id
    if req.proxy_geo:
        opts["proxy_geo"] = req.proxy_geo.model_dump()
    return opts


async def _fetch_seed_html(
    req: MapRequest,
    seed: str,
    http_client,
    scraper_fetch: ScraperFetch | None,
    *,
    check_ssrf: bool = True,
) -> tuple[str | None, str, str | None]:
    """Return (html, base_url, warning).

    base_url is the post-redirect URL so relative links resolve correctly
    (matches the crawl engine). Renders via the scraper when requested, else a
    plain (optionally SSRF-guarded) httpx GET. Never raises — failures come back
    as warnings.
    """
    if req.render:
        if scraper_fetch is None:
            return None, seed, "render requested but scraper is unavailable"
        try:
            result = await scraper_fetch(
                seed, {"render": True, "raw_html": True, **_proxy_scrape_options(req)}
            )
            final_url = (result.get("meta") or {}).get("final_url") or seed
            return result.get("raw_html"), final_url, None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return None, seed, f"seed render failed: {exc}"
    # safe_get raises SSRFError or httpx errors; degrade on either.
    try:
        resp = await safe_get(http_client, seed, check_ssrf=check_ssrf)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, seed, f"seed fetch failed: {exc}"
    if resp.status_code != 200:
        return None, seed, f"seed fetch returned {resp.status_code}"
    return resp.text, str(resp.url), None


async def build_map(
    req: MapRequest,
    *,
    http_client,
    scraper_fetch: ScraperFetch | None,
    check_ssrf: bool = True,
    extra_warnings: list[str] | None = None,
) -> MapResponse:
    """Fast URL discovery: sitemap + seed-page links, scope-filtered and deduped.

    Pure orchestration with injected fetchers so it is unit-testable without a
    live scraper or network. `check_ssrf=False` when the client egresses through
    a proxy (the crawler isn't the SSRF vector then). `extra_warnings` seed the
    response's warnings (e.g. a proxy-resolution warning from the caller).
    """
    started = time.perf_counter()
    seed = str(req.seed_url)
    scope = CompositeScope(req.scope, seed)
    dedup = DedupSet()
    ordered: list[str] = []
    warnings: list[str] = list(extra_warnings or [])

    def warn(message: str) -> None:
        # Degradations must be visible server-side too, not only as response
        # warnings — silent partial results are undebuggable in production.
        warnings.append(message)
        log.warning("map %s seed=%s", message, seed)

    async def gather_sitemap() -> list[str]:
        if not req.include_sitemap:
            return []
        try:
            sitemap_urls = await discover_sitemap_urls(http_client, seed, check_ssrf=check_ssrf)
            return await collect_sitemap_urls(
                http_client,
                sitemap_urls,
                max_urls=settings.map_max_urls,
                max_sitemaps=settings.map_max_sitemaps,
                check_ssrf=check_ssrf,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            warn(f"sitemap discovery failed: {exc}")
            return []

    async def gather_page_links() -> list[str]:
        if not req.include_page_links:
            return []
        html, base_url, warning = await _fetch_seed_html(
            req, seed, http_client, scraper_fetch, check_ssrf=check_ssrf
        )
        if warning:
            warn(warning)
        return extract_links(html or "", base_url=base_url)

    # The two discovery phases are independent network work — overlap them.
    sitemap_pages, page_links = await asyncio.gather(gather_sitemap(), gather_page_links())

    def add_if_in_scope(url: str) -> None:
        # Canonicalize so /map returns the same URL shape as /crawl (the engine
        # canonicalizes before dedup/scope too). Junk locs are skipped.
        try:
            canon = canonicalize_url(url)
        except Exception:  # pylint: disable=broad-exception-caught
            return
        if scope.allows(canon, 0) and dedup.add(canon):
            ordered.append(canon)

    add_if_in_scope(seed)
    for url in sitemap_pages:
        add_if_in_scope(url)
    for url in page_links:
        add_if_in_scope(url)

    unique_in_scope = len(ordered)
    if req.search:
        needle = req.search.lower()
        ordered = [u for u in ordered if needle in u.lower()]

    returned = ordered[: req.limit]
    return MapResponse(
        seed_url=seed,
        count=len(returned),
        urls=returned,
        stats=MapStats(
            from_sitemap=len(sitemap_pages),
            from_page=len(page_links),
            unique_in_scope=unique_in_scope,
        ),
        took_ms=int((time.perf_counter() - started) * 1000),
        warnings=warnings,
    )


async def _open_proxied_client(
    scraper, req: MapRequest, warns: list[str]
) -> httpx.AsyncClient | None:
    """Resolve the upstream proxy and build a per-request client over it.

    None means "go direct" — resolution and client-construction failures
    degrade to a warning rather than 500-ing the request.
    """
    try:
        proxy_url = await scraper.resolve_proxy(
            req.proxy_type,
            req.proxy_pool_id,
            req.proxy_geo.model_dump() if req.proxy_geo else None,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.warning("map proxy resolve failed seed=%s: %s", str(req.seed_url), exc)
        warns.append(f"proxy resolve failed ({exc}); used direct")
        return None
    if not proxy_url:
        return None
    # Needs httpx[socks] for socks5; guarded so a malformed URL degrades to
    # direct rather than 500-ing.
    try:
        return httpx.AsyncClient(
            proxy=proxy_url,
            timeout=httpx.Timeout(settings.map_http_timeout_ms / 1000.0),
            follow_redirects=False,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.warning("map proxy client init failed seed=%s: %s", str(req.seed_url), exc)
        warns.append(f"proxy client init failed ({exc}); used direct")
        return None


@router.post("", response_model=MapResponse, operation_id="create_map")
async def create_map(req: MapRequest, app_req: FastapiRequest) -> MapResponse:
    state = app_req.app.state
    scraper = state.scraper_client
    warns: list[str] = []

    if req.proxy_type and req.proxy_type != "none":
        client = await _open_proxied_client(scraper, req, warns)
        if client is not None:
            # SSRF guard off: egress is via the upstream proxy, not the crawler.
            try:
                return await build_map(
                    req, http_client=client, scraper_fetch=scraper.fetch,
                    check_ssrf=False, extra_warnings=warns,
                )
            finally:
                await client.aclose()
        if not warns:
            warns.append("proxy unavailable; used direct")
        log.warning(
            "map proxy unavailable seed=%s proxy_type=%s; used direct",
            str(req.seed_url), req.proxy_type,
        )

    return await build_map(
        req, http_client=state.http_client, scraper_fetch=scraper.fetch,
        extra_warnings=warns or None,
    )
