"""The crawler ships a second copy of the address predicate. Keep them equal.

`yozh-crawler` builds a separate image and cannot import `src.*`, so one shared
module is not available. A duplicated predicate is fine; a duplicated predicate
that silently drifts is not — this file fails the moment one copy learns an
address range the other does not.

SCOPE, stated precisely because "the copies cannot drift" would be a bigger
claim than this file backs: it compares `_ip_is_public` and nothing else. The
crawler deliberately has no single-label rule, no scheme check and no IP-literal
fast path (its caller shape differs), and it puts the host into its error
message where the scraper keeps a constant. Only the address classification is
mirrored, and only that is pinned here.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.security.egress import _ip_is_public as scraper_ip_is_public

_CRAWLER_SSRF = Path(__file__).resolve().parents[2] / "yozh-crawler" / "src" / "ssrf.py"


def _load_crawler_ssrf():
    """Import the crawler's module by path. It imports stdlib only, so this
    does not need the crawler's dependencies installed."""
    spec = importlib.util.spec_from_file_location("_crawler_ssrf", _CRAWLER_SSRF)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Every address whose classification either copy could plausibly get wrong.
NON_PUBLIC = [
    "127.0.0.1",
    "10.0.2.1",
    "192.168.1.1",
    "172.16.0.1",
    "169.254.169.254",
    "100.64.0.1",       # CGNAT — stdlib calls this public
    "100.116.74.103",   # this host's tailnet address, also CGNAT
    "192.88.99.1",      # 6to4 relay anycast — stdlib calls this public
    "0.0.0.0",
    "224.0.0.1",
    "240.0.0.1",
    "::1",
    "::",
    "fe80::1",
    "fec0::1",          # IPv6 site-local — stdlib calls this public
    "fd00::1",
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
    "not-an-ip",        # unparseable must fail closed in both
    "",
]

PUBLIC = [
    "93.184.216.34",
    "8.8.8.8",
    "1.1.1.1",
    "2606:2800:220:1:248:1893:25c8:1946",
]


def test_crawler_module_is_where_this_test_thinks_it_is():
    """A moved file must fail loudly, not silently skip the parity check."""
    assert _CRAWLER_SSRF.is_file(), f"crawler SSRF guard not found at {_CRAWLER_SSRF}"


@pytest.mark.parametrize("addr", NON_PUBLIC)
def test_both_copies_refuse(addr):
    crawler = _load_crawler_ssrf()
    assert scraper_ip_is_public(addr) is False, f"scraper allows {addr}"
    assert crawler._ip_is_public(addr) is False, f"crawler allows {addr}"


@pytest.mark.parametrize("addr", PUBLIC)
def test_both_copies_allow(addr):
    crawler = _load_crawler_ssrf()
    assert scraper_ip_is_public(addr) is True, f"scraper refuses {addr}"
    assert crawler._ip_is_public(addr) is True, f"crawler refuses {addr}"
