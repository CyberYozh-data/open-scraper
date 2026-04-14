#!/usr/bin/env python3
"""
Example: GEO Targeting with City Verification

Demonstrates how to target a specific country/city via proxy_geo,
then verifies that the resulting IP address actually belongs
to the requested location using ipapi.co.

Requirements:
- CYBERYOZH_API_KEY in .env file
- Active residential rotating proxy subscription
"""

import os
import json
import re

import httpx
from dotenv import load_dotenv
from client_helpers import scrape_page, console

load_dotenv()

GEO_TARGETS = [
    {"country_code": "GB", "city": "London"},
    {"country_code": "DE", "city": "Berlin"},
    {"country_code": "US", "city": "New York"},
]


def get_ip_via_proxy(proxy_geo: dict) -> str | None:
    """Scrape httpbin.org/ip through a geo-targeted proxy and return the IP."""
    try:
        result = scrape_page(
            url="https://httpbin.org/ip",
            proxy_type="res_rotating",
            proxy_geo=proxy_geo,
            raw_html=True,
            timeout_ms=30000,
        )

        raw = result.get("raw_html", "")
        if not raw:
            console.print("  [red]✗ No response[/red]")
            return None

        # httpbin may wrap JSON in <pre> when rendered by Playwright
        if raw.lstrip().startswith("<"):
            match = re.search(r"<pre[^>]*>(.*?)</pre>", raw, re.DOTALL)
            if match:
                raw = match.group(1).strip()
            else:
                console.print("  [red]✗ Cannot parse response[/red]")
                return None

        data = json.loads(raw)
        ip = data.get("origin", "").split(",")[0].strip()
        console.print(f"  Proxy IP : [cyan]{ip}[/cyan]")
        console.print(f"  Time     : {result['took_ms']} ms")
        return ip

    except Exception as e:
        console.print(f"  [red]✗ Error: {str(e)[:120]}[/red]")
        return None


def lookup_ip_location(ip: str) -> dict | None:
    """Resolve IP geolocation via ipapi.co (free, no key required)."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"https://ipapi.co/{ip}/json/")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        console.print(f"  [red]✗ GEO lookup failed: {e}[/red]")
        return None


def check_geo(ip: str, expected: dict) -> bool:
    """Return True if the IP resolves to the expected country (and city if given)."""
    geo = lookup_ip_location(ip)
    if not geo:
        return False

    actual_country = geo.get("country_code", "").upper()
    actual_city = geo.get("city", "")

    console.print(f"  Actual   : [yellow]{actual_city}, {actual_country}[/yellow]")

    country_ok = actual_country == expected["country_code"].upper()
    city_ok = (
        expected.get("city", "").lower() in actual_city.lower()
        if expected.get("city")
        else True
    )

    if country_ok and city_ok:
        console.print("  [green]✓ GEO matches![/green]")
        return True
    elif country_ok:
        console.print(
            f"  [yellow]⚠ Country matches but city differs "
            f"(expected {expected.get('city')}, got {actual_city})[/yellow]"
        )
        return False
    else:
        console.print(
            f"  [red]✗ GEO mismatch "
            f"(expected {expected['country_code']}, got {actual_country})[/red]"
        )
        return False


def main():
    console.print("[bold]Example: GEO Targeting with City Verification[/bold]\n")

    if not os.getenv("CYBERYOZH_API_KEY"):
        console.print("[red]⚠ CYBERYOZH_API_KEY not set in .env file[/red]")
        return

    console.print("[green]✓ CyberYozh API key found[/green]\n")

    results = []

    for target in GEO_TARGETS:
        label = f"{target.get('city', '')}, {target['country_code']}"
        console.print(f"[bold cyan]Target: {label}[/bold cyan]")
        console.print(f"  proxy_geo: {target}")

        ip = get_ip_via_proxy(target)
        if ip:
            ok = check_geo(ip, target)
            results.append((label, ok))
        else:
            results.append((label, False))

        console.print()

    # Summary
    console.print("[bold]Summary:[/bold]")
    for label, ok in results:
        status = "[green]✓ pass[/green]" if ok else "[red]✗ fail[/red]"
        console.print(f"  {label:25s} {status}")


if __name__ == "__main__":
    main()
