#!/usr/bin/env python3
"""
Example: Batch Scraping Multiple URLs

Demonstrates:
- Scraping multiple URLs efficiently
- Progress tracking
- Error handling for individual pages
- Exporting results to JSON/CSV
"""

import json
import csv
from pathlib import Path
from typing import List, Dict, Any

from client_helpers import scrape_page, console


def batch_scrape(
    urls: List[str],
    extract_rules: dict = None,
    proxy_type: str = "none",
) -> List[Dict[str, Any]]:
    """
    Scrape multiple URLs in batch

    Args:
        urls: List of URLs to scrape
        extract_rules: Optional extraction rules
        proxy_type: Proxy type to use

    Returns:
        results: List of scraping results
    """
    console.print(f"[bold cyan]Batch Scraping {len(urls)} URLs[/bold cyan]\n")

    results = []
    success_count = 0

    for i, url in enumerate(urls, 1):
        console.print(f"[yellow]{i}/{len(urls)}[/yellow] {url}")

        try:
            result = scrape_page(
                url=url,
                proxy_type=proxy_type,
                extract=extract_rules,
                timeout_ms=30000,
            )

            results.append({
                "url": url,
                "success": True,
                "status_code": result["meta"]["status_code"],
                "data": result.get("data"),
                "took_ms": result["took_ms"],
            })

            success_count += 1
            console.print(f"  [green]✓[/green] Success ({result['took_ms']}ms)\n")

        except Exception as e:
            console.print(f"  [red]✗[/red] Failed: {str(e)[:80]}\n")
            results.append({
                "url": url,
                "success": False,
                "error": str(e),
            })

    console.print(f"[bold]Summary:[/bold] {success_count}/{len(urls)} succeeded\n")
    return results


def save_results(results: List[Dict], format: str = "json"):
    """
    Save results to file

    Args:
        results: Results to save
        format: Output format (json or csv)
    """
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    if format == "json":
        filepath = output_dir / "batch_results.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        console.print(f"[green]✓ Saved to {filepath}[/green]")

    elif format == "csv":
        filepath = output_dir / "batch_results.csv"
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            if results:
                writer = csv.DictWriter(f, fieldnames=results[0].keys())
                writer.writeheader()
                writer.writerows(results)
        console.print(f"[green]✓ Saved to {filepath}[/green]")


def main():
    console.print("[bold]Example 5: Batch Scraping[/bold]\n")

    # Example 1: Scrape multiple news sites
    news_urls = [
        "https://news.ycombinator.com/",
        "https://www.reddit.com/r/programming/",
        "https://dev.to/",
    ]

    results = batch_scrape(
        urls=news_urls,
        extract_rules={
            "type": "css",
            "fields": {
                "title": {
                    "selector": "title",
                    "attr": "text",
                },
                "headings": {
                    "selector": "h1, h2",
                    "attr": "text",
                    "all": True,
                },
            }
        }
    )

    # Save results
    save_results(results, format="json")

    # Display sample
    console.print("[bold cyan]Sample Results:[/bold cyan]")
    for result in results[:3]:
        console.print(f"\n[yellow]{result['url']}[/yellow]")
        if result.get("success"):
            console.print(f"  Status: {result['status_code']}")
            if result.get("data"):
                console.print(f"  Title: {result['data'].get('title', 'N/A')}")
                headings = result['data'].get('headings', [])
                console.print(f"  Headings: {len(headings)}")


if __name__ == "__main__":
    main()
