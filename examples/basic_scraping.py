#!/usr/bin/env python3
"""
Example: Basic scraping of a single page

The simplest example - scraping example.com
"""

from client_helpers import scrape_page, print_result, console


def main():
    console.print("[bold]Example 1: Basic Scraping[/bold]\n")

    # Scrape page
    result = scrape_page(
        url="https://example.com",
        proxy_type="none",
        device="desktop",
    )

    # Print result
    print_result(result)

    # Access data
    console.print(f"\n[green]Final URL:[/green] {result['meta']['final_url']}")
    console.print(f"[green]Status code:[/green] {result['meta']['status_code']}")


if __name__ == "__main__":
    main()
