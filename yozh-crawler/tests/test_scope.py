from __future__ import annotations

from src.schemas import CrawlScope
from src.scope import CompositeScope


SEED = "https://example.com/"


def _scope(**overrides) -> CompositeScope:
    return CompositeScope(CrawlScope(**overrides), SEED)


def test_same_domain_accepts_seed_host():
    s = _scope(mode="same-domain", max_depth=3)
    assert s.allows("https://example.com/about", depth=1)
    assert not s.allows("https://other.com/", depth=1)


def test_same_domain_rejects_subdomain():
    s = _scope(mode="same-domain", max_depth=3)
    assert not s.allows("https://blog.example.com/", depth=1)
    assert s.reason("https://blog.example.com/", 1) == "mode"


def test_same_domain_accepts_www_alias_of_bare_seed():
    # www-canonical site behind a bare-domain seed (seed 301s to www.):
    # links discovered on the www host must stay in scope.
    s = _scope(mode="same-domain", max_depth=3)
    assert s.allows("https://www.example.com/about", depth=1)
    assert not s.allows("https://wwwexample.com/", depth=1)
    assert not s.allows("https://www.other.com/", depth=1)
    assert s.reason("https://blog.example.com/", 1) == "mode"


def test_same_domain_accepts_bare_alias_of_www_seed():
    s = CompositeScope(
        CrawlScope(mode="same-domain", max_depth=3), "https://www.example.com/"
    )
    assert s.allows("https://example.com/about", depth=1)
    assert s.allows("https://www.example.com/x", depth=1)
    assert not s.allows("https://blog.example.com/", depth=1)


def test_same_domain_www_alias_requires_dotted_remainder():
    # Degenerate seeds must not pair two unrelated owners: `www.com` is a
    # registered site, not an alias of the `com` TLD (and vice versa).
    s = CompositeScope(CrawlScope(mode="same-domain", max_depth=3), "https://www.com/")
    assert not s.allows("https://com/", depth=1)
    s2 = CompositeScope(CrawlScope(mode="same-domain", max_depth=3), "https://ru/")
    assert not s2.allows("https://www.ru/", depth=1)


def test_same_domain_www_strip_is_single_and_literal():
    # Pin: exactly one leading `www.` label is an alias — `www.www.x` is a
    # different host, not a deeper alias, from either seed form.
    s = _scope(mode="same-domain", max_depth=3)
    assert not s.allows("https://www.www.example.com/", depth=1)
    s2 = CompositeScope(
        CrawlScope(mode="same-domain", max_depth=3), "https://www.example.com/"
    )
    assert not s2.allows("https://www.www.example.com/", depth=1)


def test_subdomains_accepts_subdomain():
    s = _scope(mode="subdomains", max_depth=3)
    assert s.allows("https://blog.example.com/", depth=1)
    assert s.allows("https://example.com/x", depth=1)
    assert not s.allows("https://other.com/", depth=1)


def test_all_accepts_any_http_host():
    s = _scope(mode="all", max_depth=3)
    assert s.allows("https://other.com/", depth=1)
    assert not s.allows("ftp://example.com/", depth=1)


def test_max_depth_enforced():
    s = _scope(mode="same-domain", max_depth=2)
    assert s.allows("https://example.com/x", depth=2)
    assert s.reason("https://example.com/x", 3) == "max_depth"


def test_exclude_wins_over_include():
    s = _scope(
        mode="all",
        max_depth=3,
        include_patterns=[r"/blog/"],
        exclude_patterns=[r"/blog/private/"],
    )
    assert s.allows("https://x.com/blog/post", depth=1)
    assert s.reason("https://x.com/blog/private/post", 1) == "exclude"


def test_regex_mode_requires_include_match():
    s = _scope(
        mode="regex",
        max_depth=3,
        include_patterns=[r"^https://example\.com/blog/"],
    )
    assert s.allows("https://example.com/blog/1", depth=1)
    assert s.reason("https://example.com/about", 1) == "include"
