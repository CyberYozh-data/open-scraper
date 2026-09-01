from __future__ import annotations

import os

os.environ.setdefault("TASKIQ_INMEMORY", "1")

import pytest

from src.settings import Settings
from src.proxy.models import ProxyConfig
from src.proxy.cyberyozh.client import OrderedProxy


@pytest.fixture(autouse=True)
def _no_live_dns(monkeypatch, request):
    """Unit tests must not resolve real hostnames.

    Browser navigation is egress-checked now, and the check resolves the
    target — so without this every test that calls `fetch()` or `run_warmup()`
    with a hostname silently acquires a live-DNS dependency. `ci.yml` states
    that no test in the fast gate needs network egress, and an offline machine,
    a DNS blip, or an NXDOMAIN-hijacking resolver that parks into CGNAT (which
    the predicate correctly refuses) would turn dozens of unrelated tests red.

    Answers a PUBLIC address, so a test that wants a refusal must use a literal
    or patch `getaddrinfo` itself — both of which override this. The `e2e`
    tests opt out: they run against real servers on purpose.
    """
    from src.security import egress

    egress.reset_dns_cache()
    if request.node.get_closest_marker("e2e") is None:
        monkeypatch.setattr(
            "src.security.egress._getaddrinfo",
            lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 0))],
        )
    yield
    egress.reset_dns_cache()


@pytest.fixture
def test_settings():
    """Test settings"""
    return Settings(
        host="127.0.0.1",
        port=8000,
        log_level="ERROR",
        headless=True,
        workers=1,
        queue_maxsize=10,
        cyberyozh_api_key=None,
    )


@pytest.fixture
def sample_html():
    """HTML for extractor tests"""
    return """
    <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Main Title</h1>
            <div class="content">
                <p class="text">First paragraph</p>
                <p class="text">Second paragraph</p>
                <a href="https://example.com" class="link">Link</a>
            </div>
            <div class="items">
                <div class="item" data-id="1">Item 1</div>
                <div class="item" data-id="2">Item 2</div>
                <div class="item" data-id="3">Item 3</div>
            </div>
        </body>
    </html>
    """


@pytest.fixture
def sample_proxy_config():
    """Example ProxyConfig"""
    return ProxyConfig(
        server="http://proxy.example.com:8080",
        username="user",
        password="pass",
    )


@pytest.fixture
def sample_ordered_proxy():
    """Example OrderedProxy from CyberYozh API"""
    return OrderedProxy(
        id="123",
        url="http://proxy.example.com:8080",
        login="user",
        password="pass",
        status="active",
        expired=False,
        change_ip_links=["https://api.cyberyozh.com/change-ip/123"],
        connection_host="proxy.example.com",
        connection_port=8080,
    )


@pytest.fixture
def sample_proxy_history():
    """Example response from /proxies/history/"""
    return [
        {
            "id": "1",
            "url": "http://proxy1.example.com:8080",
            "connection_login": "user1",
            "connection_password": "pass1",
            "system_status": "active",
            "expired": False,
            "change_ip_links": [],
            "connection_host": "proxy1.example.com",
            "connection_port": 8080,
        },
        {
            "id": "2",
            "url": "http://proxy2.example.com:8080",
            "connection_login": "user2",
            "connection_password": "pass2",
            "system_status": "active",
            "expired": False,
            "change_ip_links": [],
            "connection_host": "proxy2.example.com",
            "connection_port": 8080,
        },
    ]


@pytest.fixture(autouse=True)
def _fake_redis_store(monkeypatch):
    """Every app/route test gets a fresh in-process fakeredis job store: the
    lifespan's init_job_store() builds RedisJobStore around it via this seam.

    We also reset _store to None so that the next call to init_job_store()
    (triggered by TestClient lifespan startup) always builds a fresh instance
    around the patched make_redis_client — even when tests run after another
    test that already ran a lifespan.

    The session store is also reset: since init_session_store() in the app
    lifespan reuses get_job_store().client, the session store will automatically
    get the same fakeredis instance once the lifespan starts. Unit tests that
    construct RedisSessionStore directly (tests/sessions/test_store.py) do so
    with their own per-test fakeredis fixture and don't rely on this reset."""
    import fakeredis.aioredis
    import src.queue.store as store_mod
    import src.sessions.store as session_store_mod
    fake = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(store_mod, "make_redis_client", lambda url: fake)
    monkeypatch.setattr(store_mod, "_store", None)
    monkeypatch.setattr(session_store_mod, "_store", None)
    yield
