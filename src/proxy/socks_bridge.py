"""
Per-request HTTP-to-SOCKS5 bridge.

Chromium (and therefore Playwright) does not support authenticated SOCKS5
proxies. Some CyberYozh proxy types (notably residential_static and plain
mobile) are only exposed as `socks5://user:pass@host:port`, which Playwright
rejects with "Browser does not support socks5 proxy authentication".

This helper spins up a minimal HTTP CONNECT proxy (pure asyncio, backed by
``python-socks`` for the upstream dial) on 127.0.0.1 on a random free port
and forwards every CONNECT request to the upstream SOCKS5 proxy, handling
SOCKS5 authentication itself. The caller passes ``http://127.0.0.1:<port>``
to Playwright as an unauthenticated HTTP proxy and Chromium is happy.

Usage:

    async with open_socks_to_http_bridge(
        "socks5://user:pass@host:port"
    ) as local_url:
        # local_url is e.g. "http://127.0.0.1:37413"
        ...
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from typing import AsyncIterator

from python_socks.async_.asyncio import Proxy

log = logging.getLogger(__name__)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _pipe(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:  # pylint: disable=broad-except
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


def _make_handler(upstream_url: str):
    async def handle(
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(client_reader.readline(), timeout=10)
            parts = request_line.decode(errors="replace").strip().split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                client_writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                await client_writer.drain()
                return

            host_port = parts[1]
            if ":" not in host_port:
                client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await client_writer.drain()
                return
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)

            # Consume the remaining request headers.
            while True:
                line = await asyncio.wait_for(client_reader.readline(), timeout=10)
                if not line or line in (b"\r\n", b"\n"):
                    break

            proxy = Proxy.from_url(upstream_url)
            try:
                sock = await proxy.connect(
                    dest_host=host, dest_port=port, timeout=30,
                )
            except Exception as exc:  # pylint: disable=broad-except
                log.warning("bridge upstream connect failed: %s", exc)
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_writer.drain()
                return

            # Open a StreamReader/Writer pair wrapping the socks-opened socket.
            loop = asyncio.get_event_loop()
            up_reader, up_writer = await asyncio.open_connection(sock=sock)

            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()

            await asyncio.gather(
                _pipe(client_reader, up_writer),
                _pipe(up_reader, client_writer),
                return_exceptions=True,
            )
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                client_writer.write(b"HTTP/1.1 408 Request Timeout\r\n\r\n")
                await client_writer.drain()
        except Exception:  # pylint: disable=broad-except
            log.exception("bridge handler crashed")
        finally:
            with contextlib.suppress(Exception):
                client_writer.close()

    return handle


@contextlib.asynccontextmanager
async def open_socks_to_http_bridge(socks_url: str) -> AsyncIterator[str]:
    """
    Start a local HTTP CONNECT proxy that forwards to the given SOCKS5 URL.

    Yields ``http://127.0.0.1:PORT`` and tears the bridge down on exit.
    """
    if not socks_url.lower().startswith("socks5://"):
        raise ValueError(f"bridge expects a socks5:// URL, got {socks_url!r}")

    port = _pick_free_port()
    server = await asyncio.start_server(
        _make_handler(socks_url), host="127.0.0.1", port=port,
    )
    local_url = f"http://127.0.0.1:{port}"
    log.debug("socks bridge up: local=%s -> upstream=%s", local_url, socks_url)

    try:
        yield local_url
    finally:
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()
        log.debug("socks bridge closed: local=%s", local_url)
