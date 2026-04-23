#!/usr/bin/env python3
"""
Example: Concurrent Batch Scraping (new batch API)

Submits multiple pages in a single job via POST /api/v1/scrape/pages.
All pages run concurrently inside the worker pool — much faster than
scraping them one by one.

Compare with batch_scraping.py which submits pages sequentially.
"""

from client_helpers import batch_scrape_pages, console


def scrape_product_catalog() -> list:
    """
    Scrape a list of product pages concurrently in one batch job.

    Returns:
        List of scraping results (same order as the input pages)
    """
    console.print("[bold cyan]Concurrent Batch: Product Catalog[/bold cyan]\n")

    pages = [
        {
            "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
            "proxy_type": "none",
            "extract": {
                "type": "css",
                "fields": {
                    "title": {"selector": ".product_main h1", "attr": "text"},
                    "price": {"selector": ".price_color", "attr": "text"},
                    "availability": {"selector": ".availability", "attr": "text"},
                },
            },
        },
        {
            "url": "https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html",
            "proxy_type": "none",
            "extract": {
                "type": "css",
                "fields": {
                    "title": {"selector": ".product_main h1", "attr": "text"},
                    "price": {"selector": ".price_color", "attr": "text"},
                    "availability": {"selector": ".availability", "attr": "text"},
                },
            },
        },
        {
            "url": "https://books.toscrape.com/catalogue/soumission_998/index.html",
            "proxy_type": "none",
            "extract": {
                "type": "css",
                "fields": {
                    "title": {"selector": ".product_main h1", "attr": "text"},
                    "price": {"selector": ".price_color", "attr": "text"},
                    "availability": {"selector": ".availability", "attr": "text"},
                },
            },
        },
        {
            "url": "https://books.toscrape.com/catalogue/sharp-objects_997/index.html",
            "proxy_type": "none",
            "extract": {
                "type": "css",
                "fields": {
                    "title": {"selector": ".product_main h1", "attr": "text"},
                    "price": {"selector": ".price_color", "attr": "text"},
                    "availability": {"selector": ".availability", "attr": "text"},
                },
            },
        },
        {
            "url": "https://books.toscrape.com/catalogue/sapiens-a-brief-history-of-humankind_996/index.html",
            "proxy_type": "none",
            "extract": {
                "type": "css",
                "fields": {
                    "title": {"selector": ".product_main h1", "attr": "text"},
                    "price": {"selector": ".price_color", "attr": "text"},
                    "availability": {"selector": ".availability", "attr": "text"},
                },
            },
        },
    ]

    console.print(f"Submitting {len(pages)} pages as one batch job...\n")

    results = batch_scrape_pages(pages)

    console.print(f"\n[bold green]Results ({len(results)} pages):[/bold green]\n")

    for i, result in enumerate(results):
        data = result.get("data") or {}
        meta = result.get("meta", {})
        console.print(f"  {i + 1}. [cyan]{(data.get('title') or '').strip()[:60]}[/cyan]")
        console.print(f"     Price: {(data.get('price') or '').strip()}  |  "
                      f"Status: {meta.get('status_code')}  |  "
                      f"Time: {result.get('took_ms')} ms")

    return results


def main():
    console.print("[bold]Example: Concurrent Batch Scraping[/bold]\n")

    results = scrape_product_catalog()

    success = sum(1 for r in results if r.get("meta", {}).get("status_code") == 200)
    console.print(f"\n[bold]Summary:[/bold] {success}/{len(results)} pages succeeded")


if __name__ == "__main__":
    main()
