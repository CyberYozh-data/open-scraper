#!/usr/bin/env python3
"""
Example: Stealth Scraping — eBay Search Results

eBay detects headless browsers and datacenter IPs. Bypass strategy:
- stealth=True       removes headless browser fingerprints
- res_rotating       residential rotating proxy for a clean IP each request
- networkidle        waits for lazy-loaded listing cards to appear

Requirements:
  - CYBERYOZH_API_KEY in .env file
  - Active residential rotating proxy subscription
"""

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from client_helpers import scrape_page, save_screenshot, console

load_dotenv()


def scrape_ebay_search(query: str, max_items: int = 10) -> list:
    """
    Search eBay and return a list of listing dicts.

    Args:
        query:     Search term
        max_items: Max listings to return

    Returns:
        List of dicts with title, price, condition, shipping, url
    """
    url = f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}&_sop=12"
    console.print(f"[bold cyan]eBay Search:[/bold cyan] '{query}'\n")

    result = scrape_page(
        url=url,
        proxy_type="res_rotating",
        stealth=True,
        wait_until="networkidle",
        screenshot=True,
        timeout_ms=60000,
        extract={
            "type": "css",
            "fields": {
                "titles": {
                    "selector": ".s-item__title",
                    "attr": "text",
                    "all": True,
                },
                "prices": {
                    "selector": ".s-item__price",
                    "attr": "text",
                    "all": True,
                },
                "conditions": {
                    "selector": ".SECONDARY_INFO",
                    "attr": "text",
                    "all": True,
                },
                "shipping": {
                    "selector": ".s-item__shipping",
                    "attr": "text",
                    "all": True,
                },
                "links": {
                    "selector": ".s-item__link",
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
    conditions = data.get("conditions") or []
    shipping = data.get("shipping") or []
    links = data.get("links") or []

    # eBay always injects a dummy first item "Shop on eBay"
    if titles and "SHOP ON EBAY" in (titles[0] or "").upper():
        titles, prices, conditions, shipping, links = (
            titles[1:], prices[1:], conditions[1:], shipping[1:], links[1:]
        )

    if not titles:
        console.print("\n  [yellow]⚠ No listings found — page may have shown a CAPTCHA[/yellow]")
        console.print("  [dim]Rotating proxy assigns a new IP on next request — try again[/dim]")
        return []

    items = []
    count = min(len(titles), max_items)
    console.print(f"\n[bold green]Found {len(titles)} listings (showing {count}):[/bold green]\n")

    for i in range(count):
        item = {
            "title": (titles[i] if i < len(titles) else "").strip(),
            "price": (prices[i] if i < len(prices) else "").strip(),
            "condition": (conditions[i] if i < len(conditions) else "").strip(),
            "shipping": (shipping[i] if i < len(shipping) else "").strip(),
            "url": (links[i] if i < len(links) else "").split("?")[0],
        }
        items.append(item)
        console.print(f"  {i + 1}. [cyan]{item['title'][:70]}[/cyan]")
        console.print(f"     {item['price']}  |  {item['condition']}  |  {item['shipping']}")

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
