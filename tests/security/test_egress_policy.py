"""The address predicate behind every navigation site.

These tests pin the two fail-open bugs the old preset-only guard shipped with
(an empty resolver answer read as "allowed"; an unparseable one escaped as a
500) and the two address ranges neither copy knew about.
"""
from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.security.egress import (
    EGRESS_BLOCKED_ERROR,
    EgressBlocked,
    assert_http_scheme,
    assert_landing_public,
    assert_navigable,
    assert_page_public,
    make_route_guard,
    reset_dns_cache,
)
from src.settings import settings


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch):
    """Every test starts with an empty allowlist unless it says otherwise."""
    monkeypatch.setattr(settings, "egress_allow_hosts", "")


def _resolves_to(monkeypatch, *addrs: str) -> None:
    monkeypatch.setattr(
        "src.security.egress._getaddrinfo",
        lambda *a, **k: [(0, 0, 0, "", (addr, 0)) for addr in addrs],
    )
    reset_dns_cache()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.2.1:8000/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://100.64.0.1/",
        # Neither the preset guard nor the crawler's copy knew these two:
        # every stdlib predicate passes them through as "public".
        "http://[fec0::1]/",
        "http://192.88.99.1/",
        "http://0.0.0.0/",
        "http://[::]/",
    ],
)
@pytest.mark.asyncio
async def test_rejects_non_public_literals(url):
    with pytest.raises(EgressBlocked):
        await assert_navigable(url, resolve=True)


@pytest.mark.parametrize("host", ["localhost", "redis", "web-scraper", "open-crawler"])
@pytest.mark.asyncio
async def test_rejects_internal_names(monkeypatch, host):
    _resolves_to(monkeypatch, "127.0.0.1")
    with pytest.raises(EgressBlocked):
        await assert_navigable(f"http://{host}:8000/", resolve=True)


@pytest.mark.asyncio
async def test_allows_a_public_address(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    await assert_navigable("https://example.com/page", resolve=True)


@pytest.mark.asyncio
async def test_userinfo_does_not_launder_a_private_host():
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://public.example.com@127.0.0.1/", resolve=True)


@pytest.mark.asyncio
async def test_empty_resolution_fails_closed(monkeypatch):
    """An empty getaddrinfo answer is 'allowed' under `for addr in addrs:`."""
    monkeypatch.setattr("src.security.egress._getaddrinfo", lambda *a, **k: [])
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://sneaky.example/", resolve=True)


@pytest.mark.asyncio
async def test_unparseable_resolved_address_blocks_not_500(monkeypatch):
    _resolves_to(monkeypatch, "not-an-ip")
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://sneaky.example/", resolve=True)


@pytest.mark.asyncio
async def test_dns_failure_fails_closed(monkeypatch):
    def _boom(*_a, **_k):
        raise socket.gaierror("no such host")

    monkeypatch.setattr("src.security.egress._getaddrinfo", _boom)
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://sneaky.example/", resolve=True)


@pytest.mark.asyncio
async def test_one_private_answer_among_many_blocks(monkeypatch):
    """Split-horizon DNS returning a public AND a private address is blocked."""
    _resolves_to(monkeypatch, "93.184.216.34", "10.0.2.1")
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://split.example/", resolve=True)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "chrome://version", "view-source:http://x/"])
@pytest.mark.asyncio
async def test_rejects_non_http_schemes(url):
    with pytest.raises(EgressBlocked):
        await assert_navigable(url, resolve=True)
    with pytest.raises(EgressBlocked):
        assert_http_scheme(url)


@pytest.mark.asyncio
async def test_allowlist_is_empty_by_default():
    """The setting unset must not quietly permit loopback."""
    assert settings.egress_allow_hosts == ""
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://127.0.0.1:9/", resolve=True)


@pytest.mark.asyncio
async def test_allowlist_permits_the_named_host(monkeypatch):
    monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
    await assert_navigable("http://127.0.0.1:9/", resolve=True)


@pytest.mark.asyncio
async def test_allowlist_matches_exact_host_only(monkeypatch):
    monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://127.0.0.2:9/", resolve=True)


@pytest.mark.asyncio
async def test_wildcard_entry_is_not_a_kill_switch(monkeypatch):
    """`*` must be an ordinary (never-matching) name, not an off-switch."""
    monkeypatch.setattr(settings, "egress_allow_hosts", "*")
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://127.0.0.1:9/", resolve=True)


@pytest.mark.parametrize("url", ["http://2130706433/", "http://0x7f.0.0.1/", "http://127.1/"])
@pytest.mark.asyncio
async def test_alternate_loopback_spellings_are_refused_when_resolved(monkeypatch, url):
    """`ip_address` rejects all three spellings, so they are not literals here
    and fall through to DNS. glibc's `getaddrinfo` accepts inet_aton forms and
    answers 127.0.0.1 — modelled faithfully rather than left to the suite's
    public-answer stub, which would make this test assert the opposite of what
    a real resolver does."""
    _resolves_to(monkeypatch, "127.0.0.1")
    with pytest.raises(EgressBlocked):
        await assert_navigable(url, resolve=True)


@pytest.mark.parametrize("url", ["http://0x7f.0.0.1/", "http://127.1/"])
@pytest.mark.asyncio
async def test_dotted_loopback_spellings_pass_the_proxied_preflight(url):
    """A real limit, documented rather than implied away.

    On the proxied path nothing is resolved, and these are not literals to
    `ipaddress` and do contain a dot — so the pre-flight lets them through.
    What stops them is WHATWG canonicalisation in the browser, which rewrites
    them to 127.0.0.1 before the request is issued, and the route guard then
    refuses that. Load-bearing and undocumented until now: if a later change
    makes the guard trust the pre-flight alone, this is the note saying what
    breaks. `2130706433` is not here because it has no dot and the single-label
    rule catches it on both paths.
    """
    await assert_navigable(url, resolve=False)


@pytest.mark.asyncio
async def test_dotless_integer_spelling_is_refused_on_both_paths():
    for resolve in (True, False):
        with pytest.raises(EgressBlocked):
            await assert_navigable("http://2130706433/", resolve=resolve)


@pytest.mark.asyncio
async def test_proxied_path_skips_dns_but_blocks_literals_and_internal_names(monkeypatch):
    """With an upstream proxy, local DNS does not reflect the proxy's
    resolution, so we do not resolve — but literals and single-label names
    still cannot be a legitimate target."""

    def _must_not_resolve(*_a, **_k):
        raise AssertionError("getaddrinfo called on the proxied path")

    monkeypatch.setattr("src.security.egress._getaddrinfo", _must_not_resolve)
    await assert_navigable("http://sneaky.example/", resolve=False)
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://10.0.0.5/", resolve=False)
    with pytest.raises(EgressBlocked):
        await assert_navigable("http://localhost:8000/", resolve=False)


@pytest.mark.asyncio
async def test_route_guard_aborts_private_and_falls_back_on_public(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    guard = make_route_guard(resolve=True)

    public = MagicMock()
    public.request.url = "https://example.com/a.js"
    public.abort = AsyncMock()
    public.fallback = AsyncMock()
    public.continue_ = AsyncMock()
    await guard(public)
    public.fallback.assert_awaited_once()
    public.abort.assert_not_awaited()
    # `continue_()` would silently disable the asset blocker registered before
    # this handler; only `fallback()` hands the request on to it.
    public.continue_.assert_not_awaited()

    private = MagicMock()
    private.request.url = "http://10.0.2.1/admin"
    private.abort = AsyncMock()
    private.fallback = AsyncMock()
    await guard(private)
    private.abort.assert_awaited_once()
    private.fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_landing_check_walks_the_redirect_chain(monkeypatch):
    """A public final URL with a private hop in the middle must still fail.

    Built this way on purpose: checking only `page.url` cannot pass it.
    """
    _resolves_to(monkeypatch, "93.184.216.34")

    hop_private = MagicMock()
    hop_private.url = "http://10.0.2.1/internal"
    hop_private.redirected_from = None

    hop_public = MagicMock()
    hop_public.url = "https://example.com/start"
    hop_public.redirected_from = hop_private

    final = MagicMock()
    final.request.redirected_from = hop_public

    with pytest.raises(EgressBlocked):
        await assert_landing_public(final, "https://example.com/end", resolve=True)


@pytest.mark.asyncio
async def test_landing_check_passes_an_all_public_chain(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    final = MagicMock()
    final.request.redirected_from = None
    await assert_landing_public(final, "https://example.com/end", resolve=True)


@pytest.mark.asyncio
async def test_landing_check_survives_a_null_response(monkeypatch):
    """`page.goto` returns None for some navigations; that is not a failure."""
    _resolves_to(monkeypatch, "93.184.216.34")
    await assert_landing_public(None, "https://example.com/end", resolve=True)


def test_blocked_error_is_not_a_proxy_failure_needle():
    """The message must not buy the attacker a paid-exit rotation, and must
    not vary by failure reason (that would be a port oracle)."""
    from src.queue.scrape_runner import looks_like_proxy_failure

    assert looks_like_proxy_failure(None, EGRESS_BLOCKED_ERROR) is False


def test_blocked_error_carries_no_attacker_supplied_host():
    assert "127.0.0.1" not in EGRESS_BLOCKED_ERROR
    assert EGRESS_BLOCKED_ERROR == "egress_blocked: target is not a public address"


class TestOnlyEgressBlockedEscapes:
    """The invariant every caller depends on: `assert_navigable` raises
    `EgressBlocked` or nothing.

    It did not. `socket.getaddrinfo` raises `UnicodeError` (a ValueError, NOT
    an OSError) from its idna codec for an empty or over-long DNS label, and
    `urlparse` raises `ValueError` on a malformed IPv6 literal. Both escaped:
    out of `fetch()` as an unhandled crash, and — worse — out of the route
    guard, leaving the request neither aborted nor continued so the page hung
    for the full deadline and the retry layer rotated a paid exit for it.
    """

    HOSTILE = [
        "http://a..example.com/",          # empty DNS label -> UnicodeError
        "http://" + "a" * 64 + ".com/",    # over-long label -> UnicodeError
        "http://[::1/",                    # malformed IPv6 -> ValueError
        "http://[/",
        "http://%00/",
    ]

    @pytest.mark.parametrize("url", HOSTILE)
    @pytest.mark.asyncio
    async def test_assert_navigable_raises_only_egress_blocked(self, url):
        with pytest.raises(EgressBlocked):
            await assert_navigable(url, resolve=True)

    @pytest.mark.parametrize("url", HOSTILE)
    @pytest.mark.asyncio
    async def test_assert_navigable_raises_only_egress_blocked_when_proxied(self, url):
        with pytest.raises(EgressBlocked):
            await assert_navigable(url, resolve=False)

    @pytest.mark.parametrize("url", HOSTILE)
    def test_assert_http_scheme_raises_only_egress_blocked(self, url):
        """Reads like a no-op and is not: any exception that is NOT
        `EgressBlocked` propagates and fails the test, which is the whole
        contract. Some of these URLs are legitimately accepted at the scheme
        layer and refused later, so `pytest.raises` would be wrong here."""
        try:
            assert_http_scheme(url)
        except EgressBlocked:
            pass

    @pytest.mark.parametrize("url", HOSTILE)
    @pytest.mark.asyncio
    async def test_route_guard_always_settles_the_route(self, url):
        """A route left neither aborted nor continued hangs the page until the
        navigation deadline — and that message is a proxy-rotation needle."""
        guard = make_route_guard(resolve=True)
        route = MagicMock()
        route.request.url = url
        route.abort = AsyncMock()
        route.fallback = AsyncMock()
        await guard(route)
        assert route.abort.await_count + route.fallback.await_count == 1
        route.abort.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_route_guard_aborts_when_the_predicate_itself_breaks(self, monkeypatch):
        """Any unexpected failure must still settle the route, closed."""
        async def _boom(*_a, **_k):
            raise RuntimeError("predicate exploded")

        monkeypatch.setattr("src.security.egress.assert_navigable", _boom)
        guard = make_route_guard(resolve=True)
        route = MagicMock()
        route.request.url = "https://example.com/x.js"
        route.abort = AsyncMock()
        route.fallback = AsyncMock()
        await guard(route)
        route.abort.assert_awaited_once()
        route.fallback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_landing_check_fails_closed_on_an_unreadable_chain(self):
        """A hop whose `.url` is not a string means the chain cannot be
        validated. That is not a reason to allow it."""
        hop = MagicMock()
        hop.url = object()
        hop.redirected_from = None
        response = MagicMock()
        response.request.redirected_from = hop
        with pytest.raises(EgressBlocked):
            await assert_landing_public(response, "https://example.com/", resolve=True)


class TestGuardsThatHadNoTest:
    """A mutation sweep found seven in-module guards whose removal left the
    whole suite green. Untested guards are the shape this branch exists to fix,
    so they get pinned here."""

    @pytest.mark.asyncio
    async def test_a_trailing_dot_does_not_launder_an_internal_name(self):
        """`localhost.` and `localhost` are one name to a resolver. Two guards
        interlock to refuse it — the host is stripped, and the empty last label
        fails the shape check — so both legs are exercised here."""
        for resolve in (True, False):
            with pytest.raises(EgressBlocked):
                await assert_navigable("http://localhost./", resolve=resolve)

    @pytest.mark.asyncio
    async def test_an_allowlist_entry_with_a_trailing_dot_still_matches(self, monkeypatch):
        monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1.")
        await assert_navigable("http://127.0.0.1:9/", resolve=True)

    @pytest.mark.asyncio
    async def test_an_idn_whose_punycode_is_too_long_is_refused(self, monkeypatch):
        """The vector that actually reaches the `UnicodeError` arm.

        The earlier hostile table is caught by the shape check first, so that
        arm was unreachable from its own tests. This name passes the shape
        check (63 characters) and only fails inside getaddrinfo's idna codec,
        which raises UnicodeError — a ValueError, not an OSError.
        """
        monkeypatch.undo()  # use the real resolver for this one
        with pytest.raises(EgressBlocked):
            await assert_navigable("http://" + "р" * 63 + ".рф/", resolve=True)

    @pytest.mark.asyncio
    async def test_an_over_long_redirect_chain_is_refused_not_truncated(self, monkeypatch):
        """Checking a prefix of a chain and calling it validated is the
        fail-open this module exists to stop."""
        _resolves_to(monkeypatch, "93.184.216.34")
        head = None
        for _ in range(40):
            hop = MagicMock()
            hop.url = "https://example.com/hop"
            hop.redirected_from = head
            head = hop
        response = MagicMock()
        response.request.redirected_from = head
        with pytest.raises(EgressBlocked):
            await assert_landing_public(response, "https://example.com/", resolve=True)

    @pytest.mark.asyncio
    async def test_page_check_walks_sub_frames(self, monkeypatch):
        """`page.url` is the MAIN frame. A sub-frame on an internal address
        renders into the screenshot even when same-origin rules keep its html
        out of `page.content()`."""
        _resolves_to(monkeypatch, "93.184.216.34")
        page = MagicMock()
        page.url = "https://example.com/p"
        main = MagicMock(url="https://example.com/p")
        child = MagicMock(url="http://10.0.2.1/internal")
        page.frames = [main, child]
        with pytest.raises(EgressBlocked):
            await assert_page_public(page, resolve=True)

    @pytest.mark.asyncio
    async def test_page_check_ignores_a_non_http_frame_but_checks_the_others(self, monkeypatch):
        _resolves_to(monkeypatch, "93.184.216.34")
        page = MagicMock()
        page.url = "about:blank"
        page.frames = [
            MagicMock(url="about:blank"),
            MagicMock(url="http://10.0.2.1/internal"),
        ]
        with pytest.raises(EgressBlocked):
            await assert_page_public(page, resolve=True)

    @pytest.mark.asyncio
    async def test_page_check_tolerates_a_page_without_a_string_url(self, monkeypatch):
        _resolves_to(monkeypatch, "93.184.216.34")
        page = MagicMock()
        page.url = None
        page.frames = ()
        await assert_page_public(page, resolve=True)

    @pytest.mark.asyncio
    async def test_landing_check_fails_closed_on_a_non_string_page_url(self):
        """Every other url path in this module type-checks before trusting.
        This one took `page_url` on truthiness alone."""
        with pytest.raises(EgressBlocked):
            await assert_landing_public(None, object(), resolve=True)

    @pytest.mark.asyncio
    async def test_the_dns_answer_is_cached_between_calls(self, monkeypatch):
        from src.security.egress import reset_dns_cache

        reset_dns_cache()
        calls = []

        def _counting(*args, **_kw):
            calls.append(args[0])
            return [(0, 0, 0, "", ("93.184.216.34", 0))]

        monkeypatch.setattr("src.security.egress._getaddrinfo", _counting)
        for _ in range(4):
            await assert_navigable("https://cached.example/x", resolve=True)
        assert calls == ["cached.example"]

    @pytest.mark.asyncio
    async def test_a_failed_lookup_is_not_cached(self, monkeypatch):
        """A DNS blip must not pin a host into the refused state."""
        from src.security.egress import reset_dns_cache

        reset_dns_cache()
        calls = []

        def _failing(*args, **_kw):
            calls.append(args[0])
            raise OSError("blip")

        monkeypatch.setattr("src.security.egress._getaddrinfo", _failing)
        for _ in range(3):
            with pytest.raises(EgressBlocked):
                await assert_navigable("https://flaky.example/x", resolve=True)
        assert len(calls) == 3

    @pytest.mark.asyncio
    async def test_the_dns_cache_is_bounded(self, monkeypatch):
        """Unbounded, a wildcard-DNS domain gives one permanent entry per
        request — measured at 294 bytes/host, so 200k hosts is 59 MB that is
        never reclaimed."""
        from src.security import egress

        egress.reset_dns_cache()
        monkeypatch.setattr(
            "src.security.egress._getaddrinfo",
            lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 0))],
        )
        for i in range(egress._DNS_CACHE_MAX + 50):
            await assert_navigable(f"https://h{i}.example/x", resolve=True)
        assert len(egress._dns_cache) <= egress._DNS_CACHE_MAX
