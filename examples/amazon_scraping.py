#!/usr/bin/env python3
"""
Example: Stealth Scraping — Amazon Product Page

Amazon uses aggressive bot detection. To bypass it:
- stealth=True       patches navigator.webdriver, WebGL and Canvas fingerprint
- res_rotating       residential IP — datacenter IPs are instantly blocked
- networkidle        waits until all JS requests finish (prices load asynchronously)

Requirements:
  - CYBERYOZH_API_KEY in .env file
  - Active residential rotating proxy subscription
"""

import os
from dotenv import load_dotenv
from client_helpers import scrape_page, save_screenshot, console

load_dotenv()


def scrape_amazon_product(url: str) -> dict:
    """
    Scrape a single Amazon product page and return extracted fields.

    Args:
        url: Amazon product URL (e.g. https://www.amazon.com/dp/B09B8YWXDF)

    Returns:
        Extracted product data dict
    """
    console.print(f"[bold cyan]Amazon Product[/bold cyan]")
    console.print(f"[dim]{url}[/dim]\n")

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
                "title": {
                    "selector": "#productTitle",
                    "attr": "text",
                    "required": True,
                },
                "price": {
                    "selector": ".a-price .a-offscreen",
                    "attr": "text",
                },
                "rating": {
                    "selector": "#acrPopover .a-icon-alt",
                    "attr": "text",
                },
                "review_count": {
                    "selector": "#acrCustomerReviewText",
                    "attr": "text",
                },
                "availability": {
                    "selector": "#availability span",
                    "attr": "text",
                },
                "brand": {
                    "selector": "#bylineInfo",
                    "attr": "text",
                },
                "bullets": {
                    "selector": "#feature-bullets li span.a-list-item",
                    "attr": "text",
                    "all": True,
                },
            },
        },
    )

    meta = result.get("meta", {})
    console.print(f"  Status : {meta.get('status_code')}")
    console.print(f"  Time   : {result['took_ms']} ms")
    console.print(f"  IP     : {meta.get('proxy_type')} / {meta.get('applied_locale')}")

    data = result.get("data") or {}

    if data:
        console.print("\n[bold green]Product data:[/bold green]")
        console.print(f"  Title     : {(data.get('title') or '').strip()[:80]}")
        console.print(f"  Price     : {(data.get('price') or '').strip()}")
        console.print(f"  Rating    : {(data.get('rating') or '').strip()}")
        console.print(f"  Reviews   : {(data.get('review_count') or '').strip()}")
        console.print(f"  Brand     : {(data.get('brand') or '').strip()}")
        console.print(f"  Available : {(data.get('availability') or '').strip()}")
        bullets = [b.strip() for b in (data.get("bullets") or []) if b.strip()]
        if bullets:
            console.print(f"  Features  :")
            for b in bullets[:4]:
                console.print(f"    • {b[:80]}")
    else:
        console.print("\n  [yellow]⚠ No data extracted — page may have shown a CAPTCHA[/yellow]")
        console.print("  [dim]Rotating proxy assigns a new IP on next request — try again[/dim]")

    if result.get("screenshot_base64"):
        save_screenshot(result["screenshot_base64"], "amazon_product.png")

    return data


def main():
    console.print("[bold]Example: Stealth Amazon Scraping[/bold]\n")

    if not os.getenv("CYBERYOZH_API_KEY"):
        console.print("[red]⚠ CYBERYOZH_API_KEY not set[/red]")
        console.print("[dim]Set it in .env — Amazon requires a residential proxy[/dim]\n")

    # Replace with any Amazon product URL
    product_url = "https://www.amazon.com/Grand-Theft-Auto-Korean-PlayStation-5/dp/B09X4GSGSC"

    scrape_amazon_product(product_url)


if __name__ == "__main__":
    main()
