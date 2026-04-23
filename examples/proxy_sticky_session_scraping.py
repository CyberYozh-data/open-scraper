#!/usr/bin/env python3
"""
Example: Proxy — Sticky Sessions & Pool Pinning

Demonstrates advanced proxy features:
- res_static        residential static proxy — same IP across all requests (sticky session)
- proxy_pool_id     pin a res_rotating job to a specific source proxy from your pool
- list available    fetch your proxy list from the API to find pool IDs

Requirements:
  - CYBERYOZH_API_KEY in .env file
  - Active residential proxy subscription (static or rotating)
"""

import os
import re
import json

import httpx
from dotenv import load_dotenv
from client_helpers import scrape_page, console, API_BASE

load_dotenv()


def list_proxies(proxy_type: str) -> list:
    """Fetch available proxies of a given type from the API."""
    with httpx.Client(timeout=15.0) as client:
        response = client.get(f"{API_BASE}/proxies/available", params={"proxy_type": proxy_type})
        response.raise_for_status()
    data = response.json()
    return data.get("items") or []


def get_ip(proxy_type: str, proxy_pool_id: str | None = None) -> str | None:
    """Return the exit IP seen by httpbin.org through the given proxy."""
    try:
        result = scrape_page(
            url="https://httpbin.org/ip",
            proxy_type=proxy_type,
            proxy_pool_id=proxy_pool_id,
            raw_html=True,
            timeout_ms=30000,
        )
        raw = result.get("raw_html") or ""
        if not raw:
            return None
        if raw.lstrip().startswith("<"):
            m = re.search(r"<pre[^>]*>(.*?)</pre>", raw, re.DOTALL)
            raw = m.group(1).strip() if m else ""
        return json.loads(raw).get("origin", "").split(",")[0].strip() if raw else None
    except Exception as e:
        console.print(f"  [red]✗ {e}[/red]")
        return None


def demo_sticky_session():
    """
    res_static proxy keeps the same IP for all requests in a session.
    Useful when the target site ties state (auth, cart) to an IP.
    """
    console.print("[bold cyan]Sticky Session (res_static)[/bold cyan]")
    console.print("[dim]Same IP should appear across all 3 requests[/dim]\n")

    ips = []
    for i in range(1, 4):
        console.print(f"  Request {i}: ", end="")
        ip = get_ip("res_static")
        if ip:
            console.print(f"[cyan]{ip}[/cyan]")
            ips.append(ip)

    if len(set(ips)) == 1:
        console.print(f"\n  [green]✓ Sticky! Same IP across all {len(ips)} requests[/green]")
    elif ips:
        console.print(f"\n  [yellow]⚠ IPs varied — check your static proxy subscription[/yellow]")


def demo_pool_pinning():
    """
    proxy_pool_id pins a rotating job to one specific source proxy.
    Useful to test a particular exit node or keep geo consistent.
    """
    console.print("\n[bold cyan]Pool Pinning (proxy_pool_id)[/bold cyan]")

    proxies = list_proxies("res_rotating")
    if not proxies:
        console.print("  [yellow]⚠ No res_rotating proxies found in your account[/yellow]")
        return

    proxy = proxies[0]
    pool_id = proxy.get("id")
    console.print(f"  Using pool ID: [cyan]{pool_id}[/cyan]  ({proxy.get('host')})\n")

    console.print("  Request 1: ", end="")
    ip1 = get_ip("res_rotating", proxy_pool_id=pool_id)
    if ip1:
        console.print(f"[cyan]{ip1}[/cyan]")

    console.print("  Request 2: ", end="")
    ip2 = get_ip("res_rotating", proxy_pool_id=pool_id)
    if ip2:
        console.print(f"[cyan]{ip2}[/cyan]")

    if ip1 and ip2:
        note = "[dim]IPs may still rotate within the pool[/dim]"
        console.print(f"\n  Pinned to pool {pool_id}. {note}")


def main():
    console.print("[bold]Example: Proxy Sticky Sessions & Pool Pinning[/bold]\n")

    if not os.getenv("CYBERYOZH_API_KEY"):
        console.print("[red]⚠ CYBERYOZH_API_KEY not set in .env[/red]\n")
        return

    demo_sticky_session()
    demo_pool_pinning()


if __name__ == "__main__":
    main()
