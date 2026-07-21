"""SSRF guard for the /map direct-egress path.

Unlike the crawl path (which fetches through the scraper service), /map makes
raw HTTP requests from the crawler container to user-supplied URLs. This module
resolves the target host and refuses private / loopback / link-local / reserved
addresses, and follows redirects manually so each hop is re-validated.

Residual risk: DNS rebinding between this check and httpx's own connect-time
resolution is not fully closed (would require pinning the resolved IP onto the
connection). This blocks the common metadata/internal-service SSRF vectors.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse


log = logging.getLogger(__name__)

# Carrier-grade NAT (shared-ISP address space). Python's ipaddress never
# classifies 100.64.0.0/10 as private (verified through 3.14), so a target
# resolving into CGNAT/Tailscale space reachable from this host would otherwise
# pass as "public". Mirrors the scraper's preset sample-fetch guard
# (src/presets/service.py).
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


class SSRFError(Exception):
    """Raised when a target resolves to a non-public address."""


async def _resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _ip_is_public(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # Unwrap IPv4-mapped IPv6 (::ffff:a.b.c.d) so a mapped private/link-local
    # address is classified by its IPv4 value regardless of Python version.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (ip.version == 4 and ip in _CGNAT_NET)
    )


async def host_is_public(host: str) -> bool:
    """True only if every resolved address for `host` is a public IP."""
    try:
        ips = await _resolve(host)
    except (socket.gaierror, OSError):
        return False
    return bool(ips) and all(_ip_is_public(ip) for ip in ips)


async def safe_get(client, url: str, *, check_ssrf: bool = True, max_redirects: int = 5):
    """GET `url`, validating the host (and every redirect hop) is public.

    Raises SSRFError on a non-public host or too many redirects. The client
    must have follow_redirects disabled — redirects are followed here so each
    hop is checked.

    When `check_ssrf` is False (the request egresses through an upstream proxy,
    so the crawler isn't the SSRF vector and local DNS doesn't reflect the
    proxy's resolution), the host check is skipped and the client follows
    redirects itself.
    """
    if not check_ssrf:
        return await client.get(url, follow_redirects=True)
    current = url
    for _ in range(max_redirects + 1):
        host = urlparse(current).hostname
        if not host or not await host_is_public(host):
            raise SSRFError(f"blocked non-public host: {host}")
        resp = await client.get(current, follow_redirects=False)
        location = resp.headers.get("location") if resp.headers else None
        if getattr(resp, "is_redirect", False) and location:
            current = urljoin(str(resp.url), location)
            continue
        return resp
    raise SSRFError("too many redirects")
