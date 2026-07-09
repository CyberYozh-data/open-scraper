"""Per-request max_retries override (ScrapeRequest.max_retries)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas import ScrapeRequest
from src.proxy.cyberyozh.provider import CyberYozhProxyProvider
from src.proxy.cyberyozh.session import CyberYozhSession
from src.proxy.resolver import DirectSession


def test_scrape_request_max_retries_default_none():
    assert ScrapeRequest(url="https://example.com").max_retries is None


def test_scrape_request_max_retries_accepts_in_range():
    assert ScrapeRequest(url="https://example.com", max_retries=1).max_retries == 1
    assert ScrapeRequest(url="https://example.com", max_retries=10).max_retries == 10


@pytest.mark.parametrize("bad", [0, -1, 11, 100])
def test_scrape_request_max_retries_rejects_out_of_range(bad):
    with pytest.raises(ValidationError):
        ScrapeRequest(url="https://example.com", max_retries=bad)


def test_provider_max_attempts_uses_override():
    provider = CyberYozhProxyProvider(client=None)  # max_attempts never touches the client
    assert provider.max_attempts("res_rotating", override=2) == 2
    assert provider.max_attempts("res_rotating", override=1) == 1
    # Floor at 1 even if a stray <1 value slips through.
    assert provider.max_attempts("res_rotating", override=0) == 1


def test_provider_max_attempts_falls_back_to_settings(monkeypatch):
    provider = CyberYozhProxyProvider(client=None)
    monkeypatch.setattr("src.proxy.cyberyozh.provider.settings.max_retries", 4)
    assert provider.max_attempts("res_rotating", override=None) == 4


def test_session_threads_override_to_attempts():
    provider = CyberYozhProxyProvider(client=None)
    session = CyberYozhSession(
        provider=provider, proxy_type_raw="res_rotating", proxy_pool_id=None, max_retries=2
    )
    assert session.max_attempts() == 2


def test_session_without_override_uses_settings(monkeypatch):
    provider = CyberYozhProxyProvider(client=None)
    monkeypatch.setattr("src.proxy.cyberyozh.provider.settings.max_retries", 6)
    session = CyberYozhSession(
        provider=provider, proxy_type_raw="res_rotating", proxy_pool_id=None
    )
    assert session.max_attempts() == 6


def test_direct_session_ignores_retries():
    # Direct (no-proxy) sessions never retry, regardless of any requested value.
    assert DirectSession().max_attempts() == 1
