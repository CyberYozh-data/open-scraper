"""The single address predicate for every outbound navigation.

Before this module the only SSRF guard in the tree sat in
`src/presets/service.py` and was called from exactly one place — the preset
sample fetch. The four real navigation sites (the main `page.goto`, the warmup
goto, the Camoufox goto and the login goto) had none, so `POST /api/v1/scrape/
page` with an internal URL reached the docker network, the compose gateway (the
host itself) and the host's tailnet, and `GET /api/v1/scrape/{job_id}/results`
handed the body back.

Two fail-open bugs in that older copy are fixed here rather than carried over:

  * it iterated `for addr in addrs:` with no emptiness check, so an empty
    `getaddrinfo` answer fell out of the loop and returned "allowed";
  * it called `ipaddress.ip_address(addr)` outside any `try`, so an
    unparseable resolver answer escaped as a `ValueError` — an HTTP 500
    rather than a refusal.

`yozh-crawler/src/ssrf.py` is the deliberate second copy of this predicate: it
ships in a separate image and cannot import `src.*`. `tests/security/
test_policy_parity.py` runs the same vector table through both so the two
cannot drift.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from collections import OrderedDict
from urllib.parse import urlparse

from src.settings import settings

log = logging.getLogger(__name__)

# Bound once at import so tests can replace THIS name. Patching
# `egress.socket.getaddrinfo` instead would replace the attribute on the stdlib
# socket module — `egress.socket is socket` — and silently reach httpx,
# fakeredis and http.server in every test that ran alongside.
_getaddrinfo = socket.getaddrinfo

# The caller-visible failure string. It is a CONSTANT and it names no host on
# purpose. Two reasons, both learned the hard way:
#   * `looks_like_proxy_failure` (src/queue/scrape_runner.py) rotates a paid
#     exit on substrings like "dns", "proxy", "timeout". A message carrying an
#     attacker-chosen hostname lets that attacker spend the proxy pool.
#   * a message that varied by failure reason (refused / no route / blocked)
#     would be a port oracle for the internal network.
# The detail goes to the log, which the caller never sees.
EGRESS_BLOCKED_ERROR = "egress_blocked: target is not a public address"

# Carrier-grade NAT. Python's `ipaddress` never classifies 100.64.0.0/10 as
# private (verified through 3.14), and this host's own default route lives in
# it, so without this a CGNAT/Tailscale target passes as public.
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
# 6to4 relay anycast and IPv6 site-local: both pass every stdlib predicate.
# `fec0::/10` is deprecated but still routed on plenty of internal networks.
_6TO4_RELAY_NET = ipaddress.ip_network("192.88.99.0/24")
_SITE_LOCAL_V6_NET = ipaddress.ip_network("fec0::/10")

# A redirect chain longer than this is not a legitimate navigation; the cap
# stops a malicious chain from turning the landing check into a DNS amplifier.
_MAX_CHAIN_HOPS = 20

# Positive-only DNS cache. Without it the route guard pays a resolver round
# trip per sub-resource on the direct path, and a page full of blackholed
# hostnames occupies executor threads for the resolver timeout.
_DNS_TTL_S = 30.0
# Capped: without a bound, a wildcard-DNS domain (or a page whose sub-resources
# all live on distinct hostnames) leaves one permanent entry per request —
# measured at ~294 bytes each, so 200k hosts is 59 MB that is never reclaimed.
# Oldest-first eviction; the cache is an optimisation, so losing an entry costs
# one lookup and nothing else.
_DNS_CACHE_MAX = 2048
_dns_cache: "OrderedDict[str, tuple[float, list[str]]]" = OrderedDict()


def reset_dns_cache() -> None:
    """For tests. Production has no reason to drop a 30-second cache."""
    _dns_cache.clear()


class EgressBlocked(Exception):
    """A navigation target is not a public address. Never leaks the host."""


def _parse(url: str):
    """`urlparse`, but a malformed URL is a refusal rather than a crash.

    `urlparse("http://[::1/")` raises `ValueError: Invalid IPv6 URL`. Left
    unwrapped that escaped every caller: out of `fetch()` as an unhandled
    error, and out of the route guard leaving the request neither aborted nor
    continued — so the page hung to the navigation deadline, and that message
    is a proxy-rotation needle. The invariant callers rely on is that this
    module raises `EgressBlocked` or nothing.
    """
    try:
        return urlparse(url)
    except ValueError as exc:
        log.warning("egress refused: unparseable URL (%s)", type(exc).__name__)
        raise EgressBlocked(EGRESS_BLOCKED_ERROR) from exc


def _allowlist() -> frozenset[str]:
    """Exact hostnames/IP literals an operator has explicitly permitted.

    Read at call time, not import time, so tests and a restart-free config
    change both take effect. Matching is exact and case-folded — there is no
    wildcard and no global off-switch. An entry of `*` is therefore an
    ordinary name that matches nothing, which is the intended behaviour: an
    operator who wants an internal target names that target.
    """
    raw = settings.egress_allow_hosts or ""
    # Trailing dots are stripped here as well as on the host being checked:
    # `example.com.` and `example.com` are one name to a resolver, and an
    # operator entry that can never match is a guard failing in the confusing
    # direction.
    return frozenset(
        entry.strip().rstrip(".").lower() for entry in raw.split(",") if entry.strip(".").strip()
    )


def _hostname_is_well_formed(host: str) -> bool:
    """Syntactic DNS validity, checked without asking a resolver.

    An empty label (`a..example.com`) or one over 63 bytes is invalid in every
    encoding, and `getaddrinfo`'s idna codec raises `UnicodeError` on both —
    which is not an `OSError`, so it used to escape as an unhandled crash.
    Checking the shape first means the two paths agree: the proxied path does
    no DNS, and without this it would accept a name the direct path refuses.

    Length only, deliberately no charset rule: an IDN host is legitimate, and
    Chromium punycodes it. A unicode label short enough here but too long once
    encoded still fails inside `_resolve`, where it is caught.
    """
    if not host or len(host) > 253:
        return False
    return all(1 <= len(label) <= 63 for label in host.split("."))


def _ip_is_public(ip_str: str) -> bool:
    """False for anything an outbound scrape has no business reaching.

    Fails closed on an unparseable value: the resolver answer that cannot be
    read is exactly the one not to trust.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # Unwrap IPv4-mapped IPv6 (::ffff:a.b.c.d) so a mapped private or
    # link-local address is judged by its IPv4 value on every Python version.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # Membership tests are version-specific: `IPv6Address in IPv4Network`
    # raises rather than returning False.
    if ip.version == 4:
        if ip in _CGNAT_NET or ip in _6TO4_RELAY_NET:
            return False
    elif ip in _SITE_LOCAL_V6_NET:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_http_scheme(url: str) -> None:
    """Reject anything that is not http/https.

    Separate from `assert_navigable` because `file://` and `chrome://` make no
    TCP connection at all: they are invisible to an address check and to the
    transport guard, so the scheme is the only place they can be caught.
    """
    scheme = _parse(url).scheme.lower()
    if scheme not in ("http", "https"):
        log.warning("egress refused: unsupported scheme %r", scheme)
        raise EgressBlocked(EGRESS_BLOCKED_ERROR)


async def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to, or `EgressBlocked` if it resolves to
    nothing. Runs in the default executor: `getaddrinfo` blocks, and this is
    called from the event loop that is also driving the browser."""
    cached = _dns_cache.get(host)
    if cached is not None and time.monotonic() - cached[0] < _DNS_TTL_S:
        return cached[1]

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(
            None, lambda: _getaddrinfo(host, None, type=socket.SOCK_STREAM)
        )
    # `UnicodeError` is NOT an `OSError`: getaddrinfo's idna codec raises it
    # for an empty label (`a..example.com`) or one over 63 bytes, and pydantic
    # `HttpUrl` accepts both, so this arrives straight from an unauthenticated
    # request body.
    except (OSError, UnicodeError) as exc:
        log.warning(
            "egress refused: cannot resolve host %r (%s)", host, type(exc).__name__
        )
        raise EgressBlocked(EGRESS_BLOCKED_ERROR) from exc
    # `sockaddr[0]` is the address for both AF_INET and AF_INET6; the stubs
    # type the tuple as `str | int` because later elements (port, flowinfo,
    # scope_id) are ints. `_ip_is_public` fails closed on anything unparseable,
    # so a surprise here is refused rather than trusted.
    addrs = [str(info[4][0]) for info in infos]
    # Only successful answers are cached, and only briefly: a failure must be
    # re-asked (a DNS blip must not pin a host into the refused state), and a
    # short positive TTL is what keeps the route guard from paying a resolver
    # round trip per sub-resource. This does not close TOCTOU — the browser
    # resolves again independently — and is not claimed to.
    _dns_cache[host] = (time.monotonic(), addrs)
    while len(_dns_cache) > _DNS_CACHE_MAX:
        _dns_cache.popitem(last=False)
    return addrs


async def assert_navigable(url: str, *, resolve: bool = True) -> None:
    """Raise `EgressBlocked` unless `url` is a public http/https target."""
    await resolve_navigable(url, resolve=resolve)


async def resolve_navigable(url: str, *, resolve: bool = True) -> list[str]:
    """`assert_navigable`, but hands back the addresses it validated.

    The transport guard needs them: having judged a NAME public, it must dial
    THAT address rather than resolve again, or a second lookup — a rebinding
    attack, or plain round-robin — reaches something nobody checked. Empty when
    nothing was resolved (an allowlisted host, or the proxied path), in which
    case the caller has no validated address to prefer and dials the name.

    `resolve=False` is for the proxied path: the request egresses through an
    upstream proxy, which does its own DNS, so a local answer describes a
    different network than the one that will be dialled. Literals and
    single-label names are still refused there — those cannot be a legitimate
    public target under any resolver, and a loopback literal handed to a
    proxied Camoufox context was measured to bypass the proxy.
    """
    assert_http_scheme(url)
    host = _parse(url).hostname
    if not host:
        log.warning("egress refused: URL has no host")
        raise EgressBlocked(EGRESS_BLOCKED_ERROR)
    # A trailing dot is the same name to a resolver but a different string to
    # an allowlist, so normalise before either is consulted.
    host = host.rstrip(".").lower()

    if host in _allowlist():
        return []

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not _ip_is_public(host):
            log.warning("egress refused: non-public literal %r", host)
            raise EgressBlocked(EGRESS_BLOCKED_ERROR)
        return [host]

    if not _hostname_is_well_formed(host):
        log.warning("egress refused: malformed hostname")
        raise EgressBlocked(EGRESS_BLOCKED_ERROR)

    # A name with no dot is a container on the compose network, `localhost`,
    # or a host-file entry — never a public site. Refused before DNS, so this
    # holds on the proxied path too.
    if "." not in host:
        log.warning("egress refused: single-label host %r", host)
        raise EgressBlocked(EGRESS_BLOCKED_ERROR)

    if not resolve:
        return []

    addrs = await _resolve(host)
    # `bool(addrs) and all(...)`, never a bare `for` loop: an empty answer must
    # not fall through as "nothing failed, therefore allowed".
    if not addrs or not all(_ip_is_public(addr) for addr in addrs):
        log.warning("egress refused: %r resolves to a non-public address", host)
        raise EgressBlocked(EGRESS_BLOCKED_ERROR)
    return addrs


async def assert_landing_public(response, page_url: str | None, *, resolve: bool = True) -> None:
    """Refuse to return content fetched through a non-public hop.

    Playwright's `context.route` handler is NOT invoked for redirect hops
    (measured on 1.57, Chromium and Firefox), so the route guard cannot see a
    302 into the internal network. This walks the chain after the fact and
    refuses the result.

    This is a SNAPSHOT of one navigation. A page that keeps navigating —
    `meta http-equiv="refresh"`, a script `location` assignment — lands
    somewhere this call never saw, which is why `assert_page_public` is called
    again immediately before the content is read.
    """
    urls: list[str] = []
    if page_url is not None:
        if not isinstance(page_url, str):
            # Every other url path here type-checks before trusting; this one
            # took truthiness alone, which is the same fail-open shape in
            # miniature.
            log.error("egress refused: page url is not a string")
            raise EgressBlocked(EGRESS_BLOCKED_ERROR)
        if page_url:
            urls.append(page_url)
    if response is not None:
        request = getattr(response, "request", None)
        if request is None:
            # Load-bearing attribute. If Playwright ever moves it, the chain
            # check silently becomes a `page_url` check — say so rather than
            # degrade quietly, which is the exact failure this module exists
            # to stop repeating.
            log.error("egress: response has no .request; redirect chain NOT checked")
        hop = getattr(request, "redirected_from", None) if request is not None else None
        hops = 0
        while hop is not None:
            if hops >= _MAX_CHAIN_HOPS:
                log.error(
                    "egress refused: redirect chain longer than %d hops; refusing "
                    "rather than checking a prefix", _MAX_CHAIN_HOPS,
                )
                raise EgressBlocked(EGRESS_BLOCKED_ERROR)
            hop_url = getattr(hop, "url", None)
            if not isinstance(hop_url, str):
                # An unreadable chain cannot be validated. Refusing is the only
                # answer that is not a guess.
                log.error("egress refused: redirect hop has no string url")
                raise EgressBlocked(EGRESS_BLOCKED_ERROR)
            urls.append(hop_url)
            hop = getattr(hop, "redirected_from", None)
            hops += 1
    for hop_url in urls:
        await assert_navigable(hop_url, resolve=resolve)


async def assert_page_public(page, *, resolve: bool = True) -> None:
    """Re-check where the page ACTUALLY is, immediately before reading it.

    `assert_landing_public` runs right after `goto` and is a snapshot. Between
    it and the content read sit `wait_for_selector` and
    `read_content_settling_navigation`, whose own docstring notes that some
    targets keep navigating after domcontentloaded. A public entry page that
    meta-refreshes to a public hop that 302s into the internal network passes
    every earlier layer — the route guard is blind to the 302 by construction —
    and was measured returning the internal body to the caller with ok=True.

    EVERY FRAME, not just `page.url`. A sub-frame pointed at a public URL that
    302s internal is invisible end to end: the route guard is not invoked for
    the redirect hop, and same-origin rules keep the frame's html out of
    `page.content()` — so it looked clean while 478,497 pixels of the internal
    page came back in `screenshot_b64` with ok=True. Measured against real
    Chromium; a rendered image of an internal page is the same disclosure as
    its markup.

    Cheap: a `page.url` read, a `page.frames` walk, and one predicate call per
    distinct URL against a cached DNS answer.
    """
    urls: list[str] = []
    current = getattr(page, "url", None)
    if isinstance(current, str) and current:
        urls.append(current)
    # A mock exposes `frames` as an empty iterable, real Playwright as a list
    # whose first entry is the main frame; both are handled by just walking it.
    for frame in getattr(page, "frames", None) or ():
        frame_url = getattr(frame, "url", None)
        if isinstance(frame_url, str) and frame_url:
            urls.append(frame_url)

    for url in dict.fromkeys(urls):
        # `about:blank` and `data:`/`blob:` are not egress and have no host to
        # judge. Skipping ONE such frame is not a bypass: every other frame is
        # still checked, so a blob main frame cannot hide an internal iframe.
        if _parse(url).scheme.lower() not in ("http", "https"):
            continue
        await assert_navigable(url, resolve=resolve)


def make_route_guard(*, resolve: bool = True):
    """A Playwright route handler that vetoes non-public sub-resources.

    Yields with `route.fallback()`, never `route.continue_()`: handlers run
    last-registered-first, and `continue_()` sends the request straight to the
    network, silently disabling the asset blocker registered before this one.
    """

    async def _guard(route) -> None:
        try:
            await assert_navigable(route.request.url, resolve=resolve)
        except EgressBlocked:
            await route.abort()
            return
        except Exception:  # pylint: disable=broad-except
            # A route left neither aborted nor continued hangs the page until
            # the navigation deadline, and THAT message is a proxy-rotation
            # needle — so an unexpected failure here would hand an attacker
            # both a DoS and a way to spend the exit pool. Settle it, closed.
            log.exception("egress guard failed; aborting the request")
            await route.abort()
            return
        await route.fallback()

    return _guard
