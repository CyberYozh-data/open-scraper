#!/usr/bin/env python3
"""
Example 8: Proxy Rotation

Simple demonstration of IP rotation with residential rotating proxies.
Shows how each request gets a different IP address.

Requirements:
- CYBERYOZH_API_KEY in .env file
- Active residential rotating proxy subscription
"""

import os
import json
import re
import time
from dotenv import load_dotenv
from client_helpers import scrape_page, console

load_dotenv()


def get_ip(proxy_type="none"):
    """Get current IP address using httpbin.org"""
    try:
        result = scrape_page(
            url="https://httpbin.org/ip",
            proxy_type=proxy_type,
            raw_html=True,
            timeout_ms=30000,
        )

        raw = result.get("raw_html", "")

        if not raw:
            console.print(f"  [red]✗ No response[/red]")
            return None

        # httpbin returns HTML with JSON inside <pre> tag
        if raw.startswith("<html"):
            match = re.search(r'<pre[^>]*>(.*?)</pre>', raw, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                data = json.loads(json_str)
            else:
                console.print(f"  [red]✗ Can't find JSON in HTML[/red]")
                return None
        else:
            data = json.loads(raw)

        ip = data.get("origin", "Unknown")

        console.print(f"  IP: [cyan]{ip}[/cyan]")
        console.print(f"  Time: {result['took_ms']}ms")
        console.print(f"  Retries: {result['meta']['retries']}")

        return ip

    except Exception as e:
        console.print(f"  [red]✗ Error: {str(e)[:100]}[/red]")
        return None


def main():
    console.print("[bold]Example 9: Proxy Rotation[/bold]\n")

    # Check API key
    if not os.getenv("CYBERYOZH_API_KEY"):
        console.print("[red]⚠ CYBERYOZH_API_KEY not set in .env file[/red]")
        return

    console.print("[green]✓ CyberYozh API key found[/green]\n")

    # 1. Show direct IP
    console.print("[bold cyan]1. Your Direct IP (no proxy):[/bold cyan]")
    direct_ip = get_ip("none")
    console.print()

    # 2. Show rotating proxy IPs
    console.print("[bold cyan]2. Rotating Proxy (5 requests):[/bold cyan]")
    console.print("[dim]Each request should get a different IP[/dim]\n")

    ips = []
    for i in range(1, 6):
        console.print(f"[yellow]Request {i}:[/yellow]")
        ip = get_ip("res_rotating")

        if ip:
            ips.append(ip)
            # Check if IP changed
            if len(ips) > 1 and ip != ips[-2]:
                console.print(f"  [green]✓ IP changed[/green]")
            elif len(ips) > 1:
                console.print(f"  [yellow]⚠ Same IP as previous[/yellow]")

        console.print()
        if i < 5:
            time.sleep(1)  # Small delay between requests

    # 3. Summary
    unique_ips = len(set(ips))
    console.print("[bold]Summary:[/bold]")
    console.print(f"  Total requests: {len(ips)}")
    console.print(f"  Unique IPs: {unique_ips}")
    console.print(f"  Rotation rate: {unique_ips}/{len(ips)}")

    if unique_ips == len(ips):
        console.print(f"  [green]✓ Perfect rotation![/green]")
    elif unique_ips > len(ips) * 0.7:
        console.print(f"  [yellow]⚠ Good rotation[/yellow]")
    else:
        console.print(f"  [red]✗ Poor rotation[/red]")


if __name__ == "__main__":
    main()
