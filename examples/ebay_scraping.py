#!/usr/bin/env python3
"""
Example: Stealth Scraping — eBay Search Results

eBay detects headless browsers and datacenter IPs. Bypass strategy:
- stealth=True       removes headless browser fingerprints
- res_rotating       residential rotating proxy for a clean IP each request
- domcontentloaded   eBay server-renders the s-card listing grid

Extraction (eBay "s-card" layout, verified 2026-07-26):
  Listings live under .srp-river-results as li.s-card--horizontal. Every field
  anchors to a per-card container so the columns stay row-aligned even when a
  card has no price. Note this collects any card in the river, including eBay's
  "Shop on eBay" dummy and /sch/ related-search suggestions — filter on the
  link if you only want item listings.

Requirements:
  - CYBERYOZH_API_KEY in .env file
  - Active residential rotating proxy subscription
"""

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from client_helpers import scrape_page, save_screenshot, console

load_dotenv()

_CARD = ".srp-river-results li.s-card--horizontal"
# The title container also holds a screen-reader-only "Opens in a new window
# or tab", so read the styled-text span rather than the container.
_TITLE = f"{_CARD} .su-card-container__header .s-card__title > span.su-styled-text"
# Direct child: each card also carries an a.s-card__link around its image.
_LINK = f"{_CARD} .su-card-container__header > a.s-card__link"


def scrape_ebay_search(query: str, max_items: int = 10) -> list:
    """
    Search eBay and return a list of listing dicts.

    Args:
        query:     Search term
        max_items: Max listings to return

    Returns:
        List of dicts with title, price, url
    """
    url = f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}&_sop=12"
    console.print(f"[bold cyan]eBay Search:[/bold cyan] '{query}'\n")

    result = scrape_page(
        url=url,
        proxy_type="res_rotating",
        stealth=True,
        wait_until="domcontentloaded",
        screenshot=True,
        timeout_ms=60000,
        extract={
            "type": "css",
            "fields": {
                "titles": {
                    "selector": _TITLE,
                    "attr": "text",
                    "all": True,
                },
                "prices": {
                    "selector": f"{_CARD} .s-card__price",
                    "attr": "text",
                    "all": True,
                },
                "links": {
                    "selector": _LINK,
                    "attr": "href",
                    "all": True,
                },
            },
        },
    )

    meta = result.get("meta", {})
    console.print(f"  Status : {meta.get('status_code')}")
    console.print(f"  Time   : {result['took_ms']} ms")

    if result.get("screenshot_base64"):
        save_screenshot(result["screenshot_base64"], "ebay_search.png")

    data = result.get("data") or {}
    titles = data.get("titles") or []
    prices = data.get("prices") or []
    links = data.get("links") or []

    if not titles:
        console.print("\n  [yellow]⚠ No listings found[/yellow]")
        console.print(
            "  [dim]Either the page was challenged, or eBay changed its card "
            "markup and the selectors above need re-checking against a live "
            "capture before you spend more proxy quota retrying[/dim]"
        )
        return []

    items = []
    count = min(len(titles), max_items)
    console.print(f"\n[bold green]Found {len(titles)} listings (showing {count}):[/bold green]\n")

    for i in range(count):
        item = {
            "title": (titles[i] if i < len(titles) else "").strip(),
            "price": (prices[i] if i < len(prices) else "").strip(),
            "url": (links[i] if i < len(links) else "").split("?")[0],
        }
        items.append(item)
        console.print(f"  {i + 1}. [cyan]{item['title'][:70]}[/cyan]")
        console.print(f"     {item['price']}  |  {item['url']}")

    return items


def main():
    console.print("[bold]Example: Stealth eBay Scraping[/bold]\n")

    if not os.getenv("CYBERYOZH_API_KEY"):
        console.print("[red]⚠ CYBERYOZH_API_KEY not set[/red]")
        console.print("[dim]Set it in .env — eBay requires a residential proxy[/dim]\n")

    items = scrape_ebay_search("gta 5 ps 5", max_items=8)

    if items:
        console.print(f"\n[green]✓ Scraped {len(items)} listings[/green]")


if __name__ == "__main__":
    main()
