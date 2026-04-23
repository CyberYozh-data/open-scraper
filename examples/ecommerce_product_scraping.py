#!/usr/bin/env python3
"""
Example: E-commerce Product Scraping

Demonstrates scraping product data:
- Product details (title, price, description)
- Images
- Reviews and ratings
- Stock availability
- Variants (size, color)

Examples from real e-commerce sites that allow scraping.
"""

from client_helpers import scrape_page, console
import json
from pathlib import Path


def scrape_books_product():
    """
    Scrape product from books.toscrape.com
    """
    console.print("[bold cyan]E-commerce Product Scraping[/bold cyan]")
    console.print("[dim]Example: Books to Scrape[/dim]\n")

    product_url = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"

    result = scrape_page(
        url=product_url,
        proxy_type="none",
        screenshot=True,
        extract={
            "type": "css",
            "fields": {
                # Product title
                "title": {
                    "selector": ".product_main h1",
                    "attr": "text",
                    "required": True,
                },
                # Price
                "price": {
                    "selector": ".price_color",
                    "attr": "text",
                    "required": True,
                },
                # Tax info
                "tax": {
                    "selector": ".tax_color",
                    "attr": "text",
                },
                # Availability
                "availability": {
                    "selector": ".availability",
                    "attr": "text",
                    "required": True,
                },
                # Number of reviews
                "review_count": {
                    "selector": ".star-rating",
                    "attr": "class",
                },
                # Product description
                "description": {
                    "selector": "#product_description ~ p",
                    "attr": "text",
                },
                # Product information table
                "upc": {
                    "selector": ".table tr:nth-child(1) td",
                    "attr": "text",
                },
                "product_type": {
                    "selector": ".table tr:nth-child(2) td",
                    "attr": "text",
                },
                # Image
                "image_url": {
                    "selector": "#product_gallery img",
                    "attr": "src",
                },
            }
        },
        timeout_ms=30000,
    )

    if result.get("data"):
        data = result["data"]

        console.print("[bold green]Product Details:[/bold green]\n")
        console.print(f"[cyan]Title:[/cyan] {data.get('title')}")
        console.print(f"[cyan]Price:[/cyan] {data.get('price')}")
        console.print(f"[cyan]Availability:[/cyan] {data.get('availability', '').strip()}")
        console.print(f"[cyan]Rating:[/cyan] {data.get('review_count')}")

        if data.get("description"):
            desc = data["description"][:150]
            console.print(f"[cyan]Description:[/cyan] {desc}...")

        console.print(f"\n[cyan]Product Info:[/cyan]")
        console.print(f"  UPC: {data.get('upc')}")
        console.print(f"  Type: {data.get('product_type')}")

        if data.get("image_url"):
            console.print(f"  Image: {data.get('image_url')}")

        # Save screenshot
        if result.get("screenshot_base64"):
            from client_helpers import save_screenshot
            save_screenshot(result["screenshot_base64"], "product.png")

        return data

    return None


def scrape_product_list(category_url: str, max_products: int = 10):
    """
    Scrape multiple products from category page
    """
    console.print(f"\n[bold cyan]Category Products Scraping[/bold cyan]")
    console.print(f"URL: {category_url}\n")

    result = scrape_page(
        url=category_url,
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
                "ratings": {
                    "selector": ".star-rating",
                    "attr": "class",
                    "all": True,
                },
                "availability": {
                    "selector": ".availability",
                    "attr": "text",
                    "all": True,
                },
                "product_links": {
                    "selector": "h3 a",
                    "attr": "href",
                    "all": True,
                },
                "images": {
                    "selector": ".image_container img",
                    "attr": "src",
                    "all": True,
                }
            }
        },
        timeout_ms=30000,
    )

    products = []

    if result.get("data"):
        data = result["data"]
        titles = data.get("titles", [])
        prices = data.get("prices", [])
        ratings = data.get("ratings", [])
        availability = data.get("availability", [])
        links = data.get("product_links", [])
        images = data.get("images", [])

        console.print(f"[green]Found {len(titles)} products[/green]\n")

        for i in range(min(len(titles), max_products)):
            product = {
                "title": titles[i] if i < len(titles) else None,
                "price": prices[i] if i < len(prices) else None,
                "rating": ratings[i] if i < len(ratings) else None,
                "availability": availability[i].strip() if i < len(availability) else None,
                "link": links[i] if i < len(links) else None,
                "image": images[i] if i < len(images) else None,
            }

            products.append(product)

            # Display
            console.print(f"{i+1}. [cyan]{product['title']}[/cyan]")
            console.print(f"   Price: {product['price']} | Rating: {product['rating']}")
            console.print(f"   Status: {product['availability']}")
            console.print()

    return products


def compare_prices_across_pages():
    """
    Compare prices across multiple pages to find best deals
    """
    console.print("[bold cyan]Price Comparison Across Categories[/bold cyan]\n")

    categories = [
        ("Travel", "https://books.toscrape.com/catalogue/category/books/travel_2/index.html"),
        ("Mystery", "https://books.toscrape.com/catalogue/category/books/mystery_3/index.html"),
        ("Science Fiction", "https://books.toscrape.com/catalogue/category/books/science-fiction_16/index.html"),
    ]

    all_products = []

    for category_name, url in categories:
        console.print(f"[yellow]Category: {category_name}[/yellow]")

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

                console.print(f"  [green]✓[/green] Found {len(titles)} products")

                for i in range(min(len(titles), 3)):  # Top 3 per category
                    price_str = prices[i] if i < len(prices) else "£0.00"
                    # Extract numeric price
                    price_num = float(price_str.replace("£", ""))

                    all_products.append({
                        "category": category_name,
                        "title": titles[i],
                        "price": price_num,
                        "price_str": price_str,
                    })

        except Exception as e:
            console.print(f"  [red]✗[/red] Error: {e}")

        console.print()

    # Sort by price
    if all_products:
        all_products.sort(key=lambda x: x["price"])

        console.print("[bold green]Best Deals (Lowest Prices):[/bold green]\n")

        for i, product in enumerate(all_products[:10], 1):
            console.print(f"{i}. [cyan]{product['title']}[/cyan]")
            console.print(f"   {product['price_str']} | Category: {product['category']}")
            console.print()

    return all_products


def scrape_with_variants():
    """
    Example of scraping products with variants (would need a real site with variants)
    This is a template for how to handle size/color variants
    """
    console.print("[bold cyan]Product Variants Scraping (Template)[/bold cyan]\n")

    console.print("[yellow]For real e-commerce with variants, extract:[/yellow]")
    console.print("  • Variant selector buttons (size, color)")
    console.print("  • Price changes per variant")
    console.print("  • Stock status per variant")
    console.print("  • SKU/product codes")
    console.print("\n[dim]Example selectors:[/dim]")
    console.print("  sizes: selector='.size-button' attr='data-size' all=True")
    console.print("  colors: selector='.color-swatch' attr='data-color' all=True")
    console.print("  stock: selector='.variant-stock' attr='text' all=True")


def main():
    console.print("[bold]Example 10: E-commerce Product Scraping[/bold]\n")

    # Example 1: Single product
    console.print("[bold]Scenario 1: Single Product Details[/bold]\n")
    product = scrape_books_product()

    # Example 2: Product list from category
    console.print("[bold]Scenario 2: Category Product List[/bold]\n")
    products = scrape_product_list(
        category_url="https://books.toscrape.com/catalogue/category/books/travel_2/index.html",
        max_products=5,
    )

    # Example 3: Price comparison
    console.print("[bold]Scenario 3: Price Comparison[/bold]\n")
    deals = compare_prices_across_pages()

    # Save results
    if products or deals:
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)

        results = {
            "single_product": product,
            "category_products": products[:5],
            "best_deals": deals[:10],
        }

        with open(output_dir / "ecommerce_results.json", "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        console.print(f"\n[green]✓ Results saved to output/ecommerce_results.json[/green]")

    # Example 4: Variants template
    scrape_with_variants()


if __name__ == "__main__":
    main()

