from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.proxy.cyberyozh.client_v2 import CyberYozhV2Client


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    r.text = "ok"
    return r


@pytest.mark.asyncio
async def test_subscription_uses_api_key_header_and_path():
    client = CyberYozhV2Client("https://app.cyberyozh.com/api/v2/rotating-proxies", "KEY")
    fake = AsyncMock()
    fake.get.return_value = _resp({"proxy_host": "gate.cyberyozh.net", "proxy_port": 10000})
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch("src.proxy.cyberyozh.client_v2.httpx.AsyncClient", return_value=cm):
        out = await client.subscription()
    assert out["proxy_host"] == "gate.cyberyozh.net"
    args, kwargs = fake.get.call_args
    assert args[0].endswith("/subscription/")
    assert kwargs["headers"]["X-Api-Key"] == "KEY"


@pytest.mark.asyncio
async def test_sub_users_unwraps_results():
    client = CyberYozhV2Client("https://app.cyberyozh.com/api/v2/rotating-proxies", "KEY")
    fake = AsyncMock()
    fake.get.return_value = _resp({"count": 1, "results": [{"id": "1", "login": "Giterfull"}]})
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch("src.proxy.cyberyozh.client_v2.httpx.AsyncClient", return_value=cm):
        out = await client.sub_users()
    assert out == [{"id": "1", "login": "Giterfull"}]


@pytest.mark.asyncio
async def test_geo_zips_city_name_optional():
    client = CyberYozhV2Client("https://app.cyberyozh.com/api/v2/rotating-proxies", "KEY")
    fake = AsyncMock()
    fake.get.return_value = _resp([{"zip": "101000", "suffix": "zip-101000"}])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch("src.proxy.cyberyozh.client_v2.httpx.AsyncClient", return_value=cm):
        await client.geo_zips("RU")  # no city → country-level zips
        params_no_city = fake.get.call_args.kwargs["params"]
        await client.geo_zips("RU", "Moscow")  # with city → narrowed
        params_city = fake.get.call_args.kwargs["params"]
    assert params_no_city == {"country_code": "RU"}  # city_name omitted, not sent empty
    assert params_city == {"country_code": "RU", "city_name": "Moscow"}
