"""`GET /api/v1/proxies/available` is anonymous and renders `ProxyItem.url`.

That field is the vendor's `url` verbatim. Everywhere else this repo treats it
as credential-bearing — it is redacted at `provider.py` before logging, and the
project's own fixture builds it as `socks5_http://USER:PASS@h.example`. Today
no proxy type returns userinfo in it, so this is latent rather than active:
one upstream response-shape change away from an anonymous credential leak.
"""
from __future__ import annotations

import pytest

from src.proxy.resolver import ProxyResolver
from src.proxy.cyberyozh.client import OrderedProxy


def _ordered(url: str) -> OrderedProxy:
    return OrderedProxy(
        id="1",
        url=url,
        login="USER",
        password="PASS",
        status="active",
        expired=False,
        change_ip_links=[],
        connection_host="h.example",
        connection_port=1080,
        access_type="private",
    )


@pytest.mark.asyncio
async def test_available_never_returns_userinfo_in_url(mocker):
    resolver = ProxyResolver()
    mocker.patch.object(
        resolver,
        "_client",
        mocker.AsyncMock(
            proxy_history=mocker.AsyncMock(
                return_value=[_ordered("socks5_http://USER:PASS@h.example:1080")]
            )
        ),
    )

    response = await resolver.list_available_proxies("res_static")
    items = response.items

    assert len(items) == 1
    assert "USER" not in items[0].url
    assert "PASS" not in items[0].url
    # The host:port is what the field is FOR, so redaction must not cost it.
    assert "h.example" in items[0].url


@pytest.mark.asyncio
async def test_credential_free_url_is_still_useful(mocker):
    resolver = ProxyResolver()
    mocker.patch.object(
        resolver,
        "_client",
        mocker.AsyncMock(
            proxy_history=mocker.AsyncMock(
                return_value=[_ordered("socks5_http://h.example:1080")]
            )
        ),
    )

    items = (await resolver.list_available_proxies("res_static")).items

    assert "h.example" in items[0].url
