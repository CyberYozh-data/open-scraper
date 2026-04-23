#!/usr/bin/env python3
"""
Example: Scraping Long Landing Page with Full-Page Screenshots

Demonstrates:
- Full-page screenshot capture
- Handling long pages with scrolling
- Saving multiple formats (HTML + Screenshot)
- Useful for QA, design review, archiving
"""

from client_helpers import scrape_page, console, save_screenshot, save_html


def capture_landing_page(
    url: str,
    save_html_file: bool = True,
) -> dict:
    """
    Capture full landing page with screenshot

    Args:
        url: Landing page URL
        save_html_file: Save HTML to file

    Returns:
        result: Scraping result with screenshot
    """
    console.print(f"[bold cyan]Capturing Landing Page[/bold cyan]")
    console.print(f"URL: {url}\n")

    result = scrape_page(
        url=url,
        proxy_type="none",
        device="desktop",
        screenshot=True,  # Full page screenshot
        raw_html=save_html_file,
        timeout_ms=60000,  # Long pages may take time
    )

    # Display info
    meta = result["meta"]
    console.print(f"[green]✓ Page loaded successfully[/green]")
    console.print(f"  Status: {meta['status_code']}")
    console.print(f"  Final URL: {meta['final_url']}")
    console.print(f"  Load time: {result['took_ms']}ms")

    # Save files
    if result.get("screenshot_base64"):
        screenshot_path = save_screenshot(
            result["screenshot_base64"],
            f"landing_{int(time.time())}.png"
        )
        console.print(f"  Screenshot size: {screenshot_path.stat().st_size // 1024} KB")

    if save_html_file and result.get("raw_html"):
        html_path = save_html(
            result["raw_html"],
            f"landing_{int(time.time())}.html"
        )
        console.print(f"  HTML size: {html_path.stat().st_size // 1024} KB")

    return result


def capture_multiple_pages(urls: list[str]):
    """
    Capture multiple landing pages

    Args:
        urls: List of URLs to capture
    """
    console.print(f"[bold cyan]Capturing {len(urls)} Landing Pages[/bold cyan]\n")

    results = []
    for i, url in enumerate(urls, 1):
        console.print(f"\n[yellow]Page {i}/{len(urls)}[/yellow]")
        try:
            result = capture_landing_page(url)
            results.append(result)
        except Exception as e:
            console.print(f"[red]✗ Failed:[/red] {e}")

    console.print(f"\n[bold green]✓ Captured {len(results)}/{len(urls)} pages[/bold green]")


def main():
    console.print("[bold]Example 4: Landing Page Screenshots[/bold]\n")

    # Example: Capture single long landing page
    capture_landing_page(
        url="https://stripe.com/payments",  # Long, beautiful landing
        save_html_file=True,
    )

    # Example: Capture multiple pages for comparison
    # Uncomment to test:
    # landing_pages = [
    #     "https://stripe.com/payments",
    #     "https://vercel.com",
    #     "https://github.com/features",
    # ]
    # capture_multiple_pages(landing_pages)


if __name__ == "__main__":
    import time

    main()
