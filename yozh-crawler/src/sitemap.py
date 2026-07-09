from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import httpx
from lxml import etree  # pylint: disable=c-extension-no-member

from .ssrf import SSRFError, safe_get


log = logging.getLogger(__name__)

# Common sitemap locations to probe when robots.txt names none.
_FALLBACK_SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml")
_MAX_XML_BYTES = 10 * 1024 * 1024  # guard against giant sitemaps


def parse_sitemap_xml(xml: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Parse a sitemap document into (page_entries, child_sitemap_urls).

    A page entry is ``(loc, lastmod)`` where ``lastmod`` is the raw ``<lastmod>``
    text (or None if absent). Handles both <urlset> (pages) and <sitemapindex>
    (nested sitemaps), namespace-agnostic. Returns empty lists on malformed input.
    """
    try:
        root = etree.fromstring(xml.encode("utf-8"))
    except (etree.XMLSyntaxError, ValueError):
        return [], []

    # A sitemap doc is either a <urlset> (pages) or a <sitemapindex> (child
    # sitemaps) — never mixed — so the root tag decides which bucket applies.
    if etree.QName(root).localname.lower() == "sitemapindex":
        child = [
            loc
            for el in root.iter()
            if etree.QName(el).localname.lower() == "loc" and (loc := (el.text or "").strip())
        ]
        return [], child

    # <urlset>: pair each <loc> with its sibling <lastmod> inside the <url> entry.
    pages: list[tuple[str, str | None]] = []
    for url_el in root.iter():
        if etree.QName(url_el).localname.lower() != "url":
            continue
        loc: str | None = None
        lastmod: str | None = None
        for child_el in url_el:
            name = etree.QName(child_el).localname.lower()
            if name == "loc":
                loc = (child_el.text or "").strip()
            elif name == "lastmod":
                lastmod = (child_el.text or "").strip() or None
        if loc:
            pages.append((loc, lastmod))
    return pages, []


def parse_robots_sitemaps(robots_txt: str, base_url: str) -> list[str]:
    """Return absolute sitemap URLs declared via `Sitemap:` lines in robots.txt."""
    out: list[str] = []
    for line in robots_txt.splitlines():
        line = line.strip()
        if not line.lower().startswith("sitemap:"):
            continue
        value = line.split(":", 1)[1].strip()
        if value:
            out.append(urljoin(base_url, value))
    return out


async def _get_text(client: httpx.AsyncClient, url: str, *, check_ssrf: bool = True) -> str | None:
    try:
        resp = await safe_get(client, url, check_ssrf=check_ssrf)
    except (httpx.HTTPError, SSRFError) as exc:
        log.debug("sitemap fetch failed url=%s err=%s", url, exc)
        return None
    if resp.status_code != 200:
        return None
    return resp.text[:_MAX_XML_BYTES]


async def discover_sitemap_urls(
    client: httpx.AsyncClient, seed_url: str, *, check_ssrf: bool = True
) -> list[str]:
    """Find sitemap URLs for a site: robots.txt directives, else common paths."""
    # robots.txt lives at the origin root; resolve declared/relative sitemap
    # URLs against the origin, not the (possibly deep) seed path.
    parsed = urlparse(seed_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    robots = await _get_text(client, f"{origin}/robots.txt", check_ssrf=check_ssrf)
    if robots:
        declared = parse_robots_sitemaps(robots, base_url=origin)
        if declared:
            return declared
    return [f"{origin}{path}" for path in _FALLBACK_SITEMAP_PATHS]


async def collect_sitemap_urls(
    client: httpx.AsyncClient,
    sitemap_urls: list[str],
    *,
    max_urls: int,
    max_sitemaps: int,
    check_ssrf: bool = True,
    match: str | None = None,
) -> list[tuple[str, str | None]]:
    """Breadth-first walk of sitemaps (following <sitemapindex>) collecting page
    entries ``(loc, lastmod)``, bounded by max_urls and the number of sitemap
    docs fetched.

    When ``match`` is given, only locs containing it (case-insensitive) are
    collected, so ``max_urls`` bounds *matching* pages rather than all pages —
    otherwise a substring search on a big sitemap is starved by the cap.
    """
    needle = match.lower() if match else None
    pages: list[tuple[str, str | None]] = []
    queue = list(sitemap_urls)
    seen: set[str] = set()
    fetched = 0
    while queue and fetched < max_sitemaps and len(pages) < max_urls:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        xml = await _get_text(client, url, check_ssrf=check_ssrf)
        if xml is None:
            continue
        fetched += 1
        found_pages, child_sitemaps = parse_sitemap_xml(xml)
        for loc, lastmod in found_pages:
            if needle is None or needle in loc.lower():
                pages.append((loc, lastmod))
        queue.extend(s for s in child_sitemaps if s not in seen)
    return pages[:max_urls]
