#!/usr/bin/env python3
"""
Example 7: Pagination

Demonstrates scraping multiple pages with pagination.
Uses books.toscrape.com as example - it has simple URL-based pagination.
"""

import time
from pathlib import Path
from client_helpers import scrape_page, console


def main():
    console.print("[bold]Example 8: Pagination[/bold]\n")
    console.print("Scraping books from multiple pages...\n")

    all_books = []

    # Scrape first 3 pages
    for page_num in range(1, 4):
        url = f"https://books.toscrape.com/catalogue/page-{page_num}.html"

        console.print(f"[yellow]Page {page_num}/3[/yellow]")

        try:
            result = scrape_page(
                url=url,
                proxy_type="none",
                extract={
                    "type": "css",
                    "fields": {
                        "titles": {
                            "selector": "h3 a",
                            "attr": "title",
                            "all": True,
                        },
                        "prices": {
                            "selector": ".price_color",
                            "attr": "text",
                            "all": True,
                        },
                    }
                },
                timeout_ms=30000,
            )

            if result.get("data"):
                titles = result["data"].get("titles", [])
                prices = result["data"].get("prices", [])

                console.print(f"  [green]✓[/green] Found {len(titles)} books")

                # Combine titles and prices
                for i, title in enumerate(titles):
                    all_books.append({
                        "page": page_num,
                        "title": title,
                        "price": prices[i] if i < len(prices) else None,
                    })

            time.sleep(1)  # Be nice to the server

        except Exception as e:
            console.print(f"  [red]✗ Error:[/red] {e}")
            break

    # Show results
    console.print(f"\n[bold green]Total: {len(all_books)} books scraped[/bold green]\n")

    # Show first 5 books
    for book in all_books[:5]:
        console.print(f"[cyan]{book['title']}[/cyan]")
        console.print(f"  Price: {book['price']}\n")

    # Save to JSON
    import json
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / "books_pagination.json"
    with open(output_file, "w") as f:
        json.dump(all_books, f, indent=2)

    console.print(f"[green]✓ Saved to {output_file}[/green]")


if __name__ == "__main__":
    main()
