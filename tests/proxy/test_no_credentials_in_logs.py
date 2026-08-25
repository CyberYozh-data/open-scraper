"""Credentials must not reach a log, an exception, or an HTTP body.

`provider.py` already carried the rule as a comment — "Never embed the raw
credential string in errors or logs — these messages travel into HTTP 502
details and service logs" — and the module broke it four lines below, twice in
the function under it, and once more in the client it calls.

Every test here drives a REAL entry point. An earlier version asserted against
an extracted helper that nothing in production called: the mutations went red,
the shipped code kept leaking.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest
from unittest.mock import patch

from src.proxy.cyberyozh.client import CyberYozhClient
from src.proxy.cyberyozh.provider import CyberYozhProxyProvider
from src.proxy.socks_bridge import open_socks_to_http_bridge
from src.utils.redaction import redact_url


USER = "SUPERSECRET-USERNAME"
PASS = "SUPERSECRET-PASSWORD"


class TestRedactUrl:
    def test_it_keeps_the_part_a_log_is_for(self):
        assert redact_url(f"socks5://{USER}:{PASS}@proxy.example.net:1080") == (
            "socks5://proxy.example.net:1080"
        )

    @pytest.mark.parametrize("url", [
        f"{USER}:{PASS}@h.example:8080",   # no scheme — what actually arrives
        "",
        "not a url at all",
        f"://{USER}:{PASS}@h.example",
    ])
    def test_it_fails_closed_on_anything_it_cannot_parse(self, url):
        """The unparseable URL is the one that reaches a redactor.

        A URL urlsplit cannot make sense of is exactly the URL that made the
        caller warn or raise. An earlier version returned such input unchanged,
        so the guard leaked at WARNING on the branch it was added to protect.
        """
        assert USER not in redact_url(url)
        assert PASS not in redact_url(url)

    def test_the_providers_own_scheme_keeps_its_host(self):
        """`socks5_http://` is what this provider actually returns.

        urlsplit only accepts [A-Za-z][A-Za-z0-9+.-]* as a scheme, so the
        underscore made it parse as a path and the whole URL degraded to the
        placeholder — safe, but it cost the host:port the log line exists for.
        """
        assert redact_url(f"socks5_http://{USER}:{PASS}@h.example:1080") == (
            "socks5_http://h.example:1080"
        )

    def test_it_drops_the_path_and_query_too(self):
        redacted = redact_url("https://api.example.com/rotate?apikey=SECRETKEY")
        assert "SECRETKEY" not in redacted
        assert redacted == "https://api.example.com"


@pytest.mark.asyncio
async def test_the_bridge_never_logs_the_credentialed_url(caplog):
    url = f"socks5://{USER}:{PASS}@proxy.example.net:1080"

    with caplog.at_level(logging.DEBUG):
        async with open_socks_to_http_bridge(url) as local:
            assert local.startswith("http://127.0.0.1:")

    assert PASS not in caplog.text
    assert USER not in caplog.text
    # Bites on deletion as well as on un-redaction: which upstream a bridge
    # points at is the whole reason the line exists.
    assert "proxy.example.net:1080" in caplog.text


@pytest.mark.asyncio
async def test_the_live_rotating_path_never_logs_the_username(caplog):
    """Drives `_to_lease`, not the parsing helper.

    The helper was extracted and then never called: `_to_lease` still ran its
    own inline copy, which logged `username=` at INFO on every parse. Mutating
    the helper turned this test red while the shipped path leaked untouched.
    """
    provider = CyberYozhProxyProvider.__new__(CyberYozhProxyProvider)
    provider.client = SimpleNamespace(
        rotating_credentials=_async_return([f"{USER}:{PASS}@gate.example.net:8080"]),
    )
    proxy = SimpleNamespace(
        id="p1", url="", connection_host="gate.example.net", connection_port=8080,
        login="account-login", password="account-password",
        country=None, city=None,
    )

    with caplog.at_level(logging.DEBUG):
        lease = await provider._to_lease("res_rotating", proxy)

    assert lease.config.username == USER, "the credential still has to reach the browser"
    assert lease.config.password == PASS
    assert USER not in caplog.text
    assert PASS not in caplog.text
    assert "gate.example.net:8080" in caplog.text, "the server is not the secret"


@pytest.mark.asyncio
async def test_a_malformed_proxy_url_is_redacted_in_the_log_and_the_error(caplog):
    """WARNING, not DEBUG — and the same URL went into a RuntimeError.

    That error is re-raised into an HTTP 502 `detail` and into a scrape job's
    caller-visible `error`, which is the exposure the module's own comment names.
    """
    provider = CyberYozhProxyProvider.__new__(CyberYozhProxyProvider)
    proxy = SimpleNamespace(
        id="p1", url=f"socks5_http://{USER}:{PASS}@h.example",  # no port
        connection_host=None, connection_port=None, country=None, city=None,
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError) as caught:
            await provider._to_lease("res_rotating", proxy)

    assert PASS not in str(caught.value) and USER not in str(caught.value)
    assert PASS not in caplog.text and USER not in caplog.text
    assert "h.example" in caplog.text, "redacted, not deleted"


@pytest.mark.asyncio
async def test_the_success_path_does_not_log_the_credentials(caplog):
    """The leak a sweep of the ERROR handling misses.

    `credentials` in the response body is a list of `user:pass@host` strings,
    and the line that logged the whole body sat on the success path. Found in
    review, after the first pass had already redacted the request payload and
    the error message around it.
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"credentials": [f"{USER}:{PASS}@gate.example.net:8080"]},
    ))
    client = CyberYozhClient(base_url="https://api.example.com", api_key="k")

    with caplog.at_level(logging.DEBUG):
        with patch(
            "src.proxy.cyberyozh.client.httpx.AsyncClient",
            return_value=httpx.AsyncClient(transport=transport),
        ):
            creds = await client.rotating_credentials({"category": "res"})

    assert creds == [f"{USER}:{PASS}@gate.example.net:8080"], "the caller still gets them"
    assert PASS not in caplog.text
    assert USER not in caplog.text
    assert "1 credentials" in caplog.text, "redacted, not deleted"


@pytest.mark.asyncio
async def test_a_provider_error_does_not_carry_the_password_into_the_502(caplog):
    """The largest instance of this defect, and the one that leaves the process.

    `client.rotating_credentials` interpolated its request payload — which holds
    connection_login/connection_password — into the exception message. The
    provider re-raises it, `/api/v1/proxies/*` turns it into a 502 `detail`, and
    the scrape path copies it into a job's `error` and `warnings`, on an
    endpoint with no service-token gate.
    """
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"detail": "upstream boom"})
    )
    client = CyberYozhClient(base_url="https://api.example.com", api_key="k")
    payload = {"connection_login": USER, "connection_password": PASS, "category": "res"}

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(httpx.HTTPStatusError) as caught:
            with patch(
                "src.proxy.cyberyozh.client.httpx.AsyncClient",
                return_value=httpx.AsyncClient(transport=transport),
            ):
                await client.rotating_credentials(payload)

    assert PASS not in str(caught.value)
    assert PASS not in caplog.text
    assert "upstream boom" in str(caught.value), "the provider's own reason must survive"


def _async_return(value):
    async def _call(*_args, **_kwargs):
        return value
    return _call
