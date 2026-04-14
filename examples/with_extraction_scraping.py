#!/usr/bin/env python3
"""
Example 5: Data Extraction with CSS Selectors

Demonstrates:
- CSS selector-based data extraction
- Extracting text, attributes, HTML
- Handling lists and single values
- Required vs optional fields
"""

from client_helpers import scrape_page, console


def extract_product_page():
    """
    Extract structured data from e-commerce product page
    """
    console.print("[bold cyan]Example: Product Page Extraction[/bold cyan]\n")

    result = scrape_page(
        url="https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        proxy_type="none",
        extract={
            "type": "css",
            "fields": {
                "title": {
                    "selector": "h1",
                    "attr": "text",
                    "required": True,
                },
                "price": {
                    "selector": ".price_color",
                    "attr": "text",
                    "required": True,
                },
                "availability": {
                    "selector": ".availability",
                    "attr": "text",
                },
                "rating": {
                    "selector": ".star-rating",
                    "attr": "class",
                },
                "description": {
                    "selector": "#product_description + p",
                    "attr": "text",
                },
                "image": {
                    "selector": "#product_gallery img",
                    "attr": "src",
                },
            }
        }
    )

    if result.get("data"):
        data = result["data"]
        console.print("[bold green]Extracted Data:[/bold green]")
        for key, value in data.items():
            console.print(f"  {key}: {value}")


def extract_article_list():
    """
    Extract list of articles from news site
    """
    console.print("\n[bold cyan]Example: Article List Extraction[/bold cyan]\n")

    result = scrape_page(
        url="https://news.ycombinator.com/",
        proxy_type="none",
        extract={
            "type": "css",
            "fields": {
                "titles": {
                    "selector": ".titleline > a",
                    "attr": "text",
                    "all": True,
                },
                "links": {
                    "selector": ".titleline > a",
                    "attr": "href",
                    "all": True,
                },
                "scores": {
                    "selector": ".score",
                    "attr": "text",
                    "all": True,
                },
            }
        }
    )

    if result.get("data"):
        data = result["data"]
        titles = data.get("titles", [])
        links = data.get("links", [])
        scores = data.get("scores", [])

        console.print(f"[bold green]Found {len(titles)} articles:[/bold green]\n")

        for i in range(min(10, len(titles))):
            console.print(f"{i + 1}. [cyan]{titles[i]}[/cyan]")
            if i < len(links):
                console.print(f"   {links[i]}")
            if i < len(scores):
                console.print(f"   {scores[i]}")
            console.print()


def extract_table_data():
    """
    Extract table data
    """
    console.print("[bold cyan]Example: Table Data Extraction[/bold cyan]\n")

    result = scrape_page(
        url="https://en.wikipedia.org/wiki/List_of_programming_languages",
        proxy_type="none",
        extract={
            "type": "css",
            "fields": {
                "language_names": {
                    "selector": ".mw-parser-output > ul > li > a:first-child",
                    "attr": "text",
                    "all": True,
                },
            }
        }
    )

    if result.get("data"):
        languages = result["data"].get("language_names", [])
        console.print(f"[bold green]Found {len(languages)} programming languages[/bold green]")
        console.print(f"[dim]First 20:[/dim]")
        for lang in languages[:20]:
            console.print(f"  • {lang}")


def main():
    console.print("[bold]Example 6: Data Extraction Patterns[/bold]\n")

    # Example 1: Single product
    extract_product_page()

    # Example 2: List of items
    extract_article_list()

    # Example 3: Table data
    # extract_table_data()


if __name__ == "__main__":
    main()
