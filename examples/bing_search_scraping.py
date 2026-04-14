#!/usr/bin/env python3
"""
Example 2: Scraping Bing Search Results

Bing is more scraper-friendly than Google and doesn't require proxy for demos.
Good for learning pagination and data extraction patterns.
"""

import time
from typing import List, Dict, Any
from urllib.parse import quote_plus

from client_helpers import scrape_page, console


def scrape_bing_search(query: str, pages: int = 5) -> List[Dict[str, Any]]:
    """
    Scrape Bing search results

    Args:
        query: Search query
        pages: Number of pages (10 results per page)

    Returns:
        results: List of search results
    """
    console.print(f"[bold cyan]Scraping Bing:[/bold cyan] '{query}' ({pages} pages)\n")

    all_results = []

    for page in range(pages):
        # Bing pagination: first=0, first=10, first=20...
        first = page * 10
        url = f"https://www.bing.com/search?q={quote_plus(query)}&first={first}"

        console.print(f"[yellow]Page {page + 1}/{pages}[/yellow] (first={first})")

        try:
            result = scrape_page(
                url=url,
                proxy_type="none",  # Bing works without proxy
                device="desktop",
                extract={
                    "type": "css",
                    "fields": {
                        "titles": {
                            "selector": "h2 a, .b_algo h2",
                            "attr": "text",
                            "all": True,
                        },
                        "links": {
                            "selector": "h2 a",
                            "attr": "href",
                            "all": True,
                        },
                        "snippets": {
                            "selector": ".b_caption p, .b_algoSlug",
                            "attr": "text",
                            "all": True,
                        },
                    },
                },
                timeout_ms=30000,
            )

            if result.get("data"):
                titles = result["data"].get("titles", [])
                links = result["data"].get("links", [])
                snippets = result["data"].get("snippets", [])

                console.print(f"  [green]✓[/green] Found {len(titles)} results")

                for i, title in enumerate(titles):
                    all_results.append({
                        "page": page + 1,
                        "position": i + 1,
                        "global_position": first + i + 1,
                        "title": title,
                        "url": links[i] if i < len(links) else None,
                        "snippet": snippets[i] if i < len(snippets) else None,
                    })

            # Small delay
            if page < pages - 1:
                time.sleep(2)

        except Exception as e:
            console.print(f"  [red]✗ Error:[/red] {e}")
            continue

    return all_results


def main():
    console.print("[bold]Example 3: Bing Search Scraping[/bold]\n")

    results = scrape_bing_search(
        query="python web scraping",
        pages=5,
    )

    if results:
        console.print(f"\n[bold green]Found {len(results)} results:[/bold green]\n")

        for result in results[:10]:
            console.print(f"{result['global_position']}. [cyan]{result['title']}[/cyan]")
            if result.get('url'):
                console.print(f"   [blue]{result['url']}[/blue]")
            if result.get('snippet'):
                console.print(f"   [dim]{result['snippet'][:100]}...[/dim]")
            console.print()


if __name__ == "__main__":
    main()
