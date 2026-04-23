#!/usr/bin/env python3
"""
Example: Scraping Huggingface Chat

Demonstrates scraping AI chat interfaces.
Note: Most ChatGPT clones use JavaScript heavily and require:
- Full page rendering
- Waiting for dynamic content
- Handling async loading
"""

from client_helpers import scrape_page, console, save_html


def scrape_huggingface_chat():
    """
    Scrape HuggingFace Chat interface (free ChatGPT alternative)
    """
    console.print("[bold cyan]Scraping HuggingFace Chat[/bold cyan]\n")

    # HuggingFace Chat main page
    url = "https://huggingface.co/chat"

    result = scrape_page(
        url=url,
        proxy_type="none",
        device="desktop",
        raw_html=True,
        extract={
            "type": "css",
            "fields": {
                "page_title": {
                    "selector": "h1, title",
                    "attr": "text",
                },
                "models": {
                    "selector": "button, .model-card, .model-name",
                    "attr": "text",
                    "all": True,
                },
                "description": {
                    "selector": "p, .description",
                    "attr": "text",
                    "all": True,
                },
            }
        },
        timeout_ms=30000,
    )

    if result.get("data"):
        console.print("[bold green]Extracted Data:[/bold green]")
        data = result["data"]

        if data.get("page_title"):
            console.print(f"Title: {data['page_title']}")

        if data.get("models"):
            console.print(f"\nAvailable models/options: {len(data['models'])}")
            for model in data['models'][:10]:
                if model.strip():
                    console.print(f"  • {model[:80]}")

    # Save HTML for analysis
    if result.get("raw_html"):
        save_html(result["raw_html"], "huggingface_chat.html")
        console.print("\n[green]✓ HTML saved for analysis[/green]")
        console.print("[dim]Check output/huggingface_chat.html to find chat elements[/dim]")


def main():
    console.print("[bold]Example 7: AI Chat Interfaces[/bold]\n")
    console.print("[dim]Note: Interactive chat requires automation (clicking, typing)[/dim]")
    console.print("[dim]This example shows scraping landing pages and available models[/dim]\n")

    # Scrape HuggingFace Chat
    scrape_huggingface_chat()

    console.print("\n[yellow]For actual chat interaction, you need:[/yellow]")
    console.print("  1. Playwright API calls (click, type, waitFor)")
    console.print("  2. WebSocket monitoring")
    console.print("  3. Session management")
    console.print("  4. This requires custom browser automation code")


if __name__ == "__main__":
    main()
