from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from src.app import create_app


def test_sub_users_strips_secrets():
    client_v2 = AsyncMock()
    client_v2.sub_users.return_value = [
        {"id": "1", "login": "Giterfull", "real_login": "Giterfull897e009c",
         "password": "SECRET", "is_primary": True, "traffic_left_mb": 5000},
    ]
    with patch("src.api.prem_proxies.proxy_resolver") as pr:
        pr._client_v2 = client_v2
        with TestClient(create_app()) as tc:
            resp = tc.get("/api/v2/prem-proxies/sub-users")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"id": "1", "login": "Giterfull", "is_primary": True}]
    assert "SECRET" not in resp.text and "real_login" not in resp.text


def test_subscription_omits_when_unconfigured():
    with patch("src.api.prem_proxies.proxy_resolver") as pr:
        pr._client_v2 = None
        with TestClient(create_app()) as tc:
            resp = tc.get("/api/v2/prem-proxies/sub-users")
    assert resp.status_code == 200
    assert resp.json() == []


def test_session_options_allowlists_keys():
    """session-options only returns the keys the UI needs; extra keys are dropped."""
    client_v2 = AsyncMock()
    client_v2.session_options.return_value = {
        "ip_filters": ["max-size-security"],
        "session_durations": [5, 10],
        "protocols": ["http"],
        "username_grammar": "{user}:{pass}",
        "internal_secret": "should_be_dropped",
        "other_field": 42,
    }
    with patch("src.api.prem_proxies.proxy_resolver") as pr:
        pr._client_v2 = client_v2
        with TestClient(create_app()) as tc:
            resp = tc.get("/api/v2/prem-proxies/session-options")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"ip_filters", "session_durations", "protocols", "username_grammar"}
    assert "internal_secret" not in body
    assert "other_field" not in body


def test_upstream_error_yields_graceful_empty():
    """An upstream exception from client_v2 returns the graceful empty shape."""
    client_v2 = AsyncMock()
    client_v2.sub_users.side_effect = RuntimeError("upstream down")
    client_v2.subscription.side_effect = RuntimeError("upstream down")
    client_v2.session_options.side_effect = RuntimeError("upstream down")

    with patch("src.api.prem_proxies.proxy_resolver") as pr:
        pr._client_v2 = client_v2
        with TestClient(create_app()) as tc:
            r_users = tc.get("/api/v2/prem-proxies/sub-users")
            r_sub = tc.get("/api/v2/prem-proxies/subscription")
            r_opts = tc.get("/api/v2/prem-proxies/session-options")

    assert r_users.status_code == 200
    assert r_users.json() == []

    assert r_sub.status_code == 200
    assert r_sub.json() == {"configured": False}

    assert r_opts.status_code == 200
    assert r_opts.json() == {}
