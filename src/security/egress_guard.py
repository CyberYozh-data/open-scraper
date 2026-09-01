"""Egress policy enforced at the transport, so a refused request is never made.

Every other layer refuses AFTER the fact. The pre-flight checks the URL the
caller asked for; the route guard sees sub-resources but — measured on
Playwright 1.57, both engines — is never invoked for a redirect hop; the
landing and read-time checks withhold content the browser has already fetched.
A public URL that 302s into the internal network therefore still fires one
internal GET, and Chromium's favicon request is issued below the level a route
handler can see at all.

A proxy in the context's `proxy=` slot has neither blind spot. Measured
directly against real Chromium before this module was written:

  * it is handed BOTH hops of a redirect —
      REQLINE GET http://entry/start
      REQLINE GET http://internal/final
    so denying the second PREVENTS the request rather than refusing its body;
  * it is handed a HOSTNAME, not an address the browser already resolved —
      REQLINE CONNECT example.com:443
    so this module resolves the name once and then dials the address it
    validated. That closes the TOCTOU / DNS-rebinding window by construction,
    which no check that merely inspects a URL can do.

Shape follows `src/proxy/socks_bridge.py`, the local HTTP proxy this codebase
has run in production for over a year: bind 127.0.0.1 on a free port, hand
Playwright `http://127.0.0.1:PORT`. It slots into the same `bridge_cm` the
caller already unwinds, so no new lifecycle appears anywhere.

Only for the DIRECT path. With an upstream proxy configured the browser already
sends everything there, our internals are unreachable that way, and local DNS
does not reflect the proxy's resolution.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import AsyncIterator
from urllib.parse import urlsplit

from src.proxy.socks_bridge import _pipe
from src.security.egress import EGRESS_BLOCKED_ERROR, EgressBlocked, resolve_navigable

log = logging.getLogger(__name__)

_HEADER_TIMEOUT_S = 10.0
# `asyncio.start_server` defaults its StreamReader to 64 KiB and `readline`
# RAISES past it. Long SERP urls and fat cookie jars are ordinary scraper
# traffic, and an unhandled ValueError there returned zero bytes to the client
# with a stack trace naming no target. Generous enough for real traffic, and
# an explicit refusal past it.
_STREAM_LIMIT = 1024 * 1024
_MAX_HEADER_BYTES = 512 * 1024
_DIAL_TIMEOUT_S = 30.0
# `denied` is surfaced to the caller as a warning. A hostile page with
# thousands of internal sub-resources must not turn that field into a payload,
# so it is deduplicated and capped.
_MAX_DENIED = 20
# ...and a cap on EACH one. `_MAX_DENIED` bounded the count only, while a
# single target can approach the request-line limit, so twenty refusals could
# become a multi-megabyte payload on its way into `warnings`.
MAX_DENIED_TARGET_CHARS = 120

# RFC 7230 token characters. The method is validated by SHAPE rather than
# against a list of familiar verbs: Fetch permits extension methods after
# preflight, and with the guard on by default an allowlist silently breaks
# WebDAV and custom HTTP that worked on the direct path.
_TOKEN_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


def _is_token(value: str) -> bool:
    return bool(value) and all(c in _TOKEN_CHARS for c in value)


def _content_length(headers: list[bytes]) -> int | None:
    """The declared body length, or None when absent/unparseable."""
    for header in headers:
        if header.lower().startswith(b"content-length:"):
            try:
                return int(header.split(b":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _has_transfer_encoding(headers: list[bytes]) -> bool:
    return any(h.lower().startswith(b"transfer-encoding:") for h in headers)

def _simple_response(status: int, reason: str) -> bytes:
    return (
        f"HTTP/1.1 {status} {reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
    ).encode()


_BLOCKED_RESPONSE = (
    "HTTP/1.1 403 Forbidden\r\n"
    "Content-Type: text/plain\r\n"
    f"Content-Length: {len(EGRESS_BLOCKED_ERROR)}\r\n"
    "Connection: close\r\n"
    "\r\n"
    f"{EGRESS_BLOCKED_ERROR}"
).encode()


class EgressGuard:
    """A running listener plus the record of what it refused."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.denied: list[str] = []

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _record(self, target: str) -> None:
        target = target[:MAX_DENIED_TARGET_CHARS]
        if target not in self.denied and len(self.denied) < _MAX_DENIED:
            self.denied.append(target)


def _target_of(method: str, target: str) -> tuple[str, int, str]:
    """(host, port, url) for a proxy request line.

    A proxy only ever receives absolute-form (`GET http://host/path`) or
    CONNECT (`CONNECT host:443`). Origin-form means something is talking to
    this listener that should not be, and is refused rather than guessed at.
    """
    if method == "CONNECT":
        if ":" not in target:
            raise ValueError("CONNECT target has no port")
        host, port_str = target.rsplit(":", 1)
        return host.strip("[]"), _port(port_str), f"https://{target}/"
    parts = urlsplit(target)
    if not parts.scheme or not parts.hostname:
        raise ValueError("expected an absolute-form request target")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("port out of range")
    return parts.hostname, port, target


def _port(raw: str) -> int:
    """A port, or `ValueError`. Unvalidated, `99999` reached
    `asyncio.open_connection` and raised `OverflowError`, which is not in the
    dial except-clause — a traceback and a zero-byte reply."""
    port = int(raw)
    if not 1 <= port <= 65535:
        raise ValueError("port out of range")
    return port


def _make_handler(guard: EgressGuard, *, resolve: bool):
    async def handle(
        client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            try:
                request_line = await asyncio.wait_for(
                    client_reader.readline(), timeout=_HEADER_TIMEOUT_S
                )
            except ValueError:
                # Past the stream limit: refuse explicitly rather than crash.
                log.warning("egress guard refused an oversized request line")
                client_writer.write(_simple_response(414, "URI Too Long"))
                await client_writer.drain()
                return
            parts = request_line.decode(errors="replace").strip().split()
            if len(parts) < 2:
                client_writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                await client_writer.drain()
                return
            method, target = parts[0].upper(), parts[1]
            if not _is_token(method):
                client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await client_writer.drain()
                return

            headers: list[bytes] = []
            header_bytes = 0
            oversized = False
            while True:
                try:
                    line = await asyncio.wait_for(
                        client_reader.readline(), timeout=_HEADER_TIMEOUT_S
                    )
                except ValueError:
                    oversized = True
                    break
                if not line or line in (b"\r\n", b"\n"):
                    break
                header_bytes += len(line)
                if header_bytes > _MAX_HEADER_BYTES:
                    oversized = True
                    break
                headers.append(line)
            if oversized:
                log.warning("egress guard refused an oversized header block")
                client_writer.write(_simple_response(431, "Request Header Fields Too Large"))
                await client_writer.drain()
                return

            if method != "CONNECT" and _has_transfer_encoding(headers):
                # Refused rather than guessed at: without a Content-Length the
                # request body has no bound, and forwarding an unbounded client
                # stream is exactly what lets a pipelined request through.
                log.warning("egress guard refused a chunked proxy request")
                client_writer.write(_simple_response(411, "Length Required"))
                await client_writer.drain()
                return

            try:
                host, port, url = _target_of(method, target)
            except ValueError:
                client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await client_writer.drain()
                return

            try:
                addrs = await resolve_navigable(url, resolve=resolve)
            except EgressBlocked:
                guard._record(f"{host}:{port}")  # pylint: disable=protected-access
                log.warning("egress guard refused %s:%s", host, port)
                client_writer.write(_BLOCKED_RESPONSE)
                await client_writer.drain()
                return

            # THE addresses, not the name. `assert_navigable` resolved them and
            # judged every one public; handing the hostname to the connector
            # would let a second lookup — rebinding, or plain round-robin —
            # reach something nobody checked. All of them are tried, because
            # `addrs[0]` alone drops multi-address failover and 502s a host
            # whose first address happens to be down.
            candidates = addrs or [host]
            upstream_reader = writer = None
            last_error: Exception | None = None
            # ONE budget for the whole dial, not one per address. A fresh
            # timeout each meant a black-holed AAAA ahead of a working A could
            # burn the caller's entire navigation deadline before the working
            # address was tried at all — and the handler kept dialling long
            # after the page had given up.
            dial_deadline = asyncio.get_running_loop().time() + _DIAL_TIMEOUT_S
            for dial_host in candidates:
                remaining = dial_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    upstream_reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(dial_host, port), timeout=remaining
                    )
                    break
                except (OSError, asyncio.TimeoutError) as exc:
                    last_error = exc
            if writer is None or upstream_reader is None:
                log.warning(
                    "egress guard upstream dial failed: %s",
                    type(last_error).__name__ if last_error else "no candidates",
                )
                with contextlib.suppress(Exception):
                    client_writer.write(_simple_response(502, "Bad Gateway"))
                    await client_writer.drain()
                return
            upstream_writer = writer

            if method == "CONNECT":
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
            else:
                path = target[len(f"{urlsplit(target).scheme}://{urlsplit(target).netloc}"):]
                rebuilt = [f"{method} {path or '/'} HTTP/1.1\r\n".encode()]
                # A plain `ws://` handshake is an ordinary GET carrying
                # `Upgrade: websocket`, and the connection it opens is by
                # definition one that CONTINUES. Forcing `Connection: close`
                # on it strips the upgrade and breaks a legitimate websocket
                # the moment the guard is on — raised on review, and the two
                # cases cannot share one rule.
                is_upgrade = any(
                    h.lower().startswith(b"upgrade:") for h in headers
                )
                for header in headers:
                    lowered = header.lower()
                    # Hop-by-hop headers are ours to set, not the client's to
                    # forward. `Connection` is dropped and re-set below.
                    if lowered.startswith((b"proxy-", b"keep-alive")):
                        continue
                    if lowered.startswith(b"connection:") and not is_upgrade:
                        continue
                    rebuilt.append(header)
                if is_upgrade:
                    # The client's own `Connection: Upgrade` was preserved
                    # above; the policy check already ran on this target, and
                    # the socket stays pinned to the address it validated.
                    rebuilt.append(b"\r\n")
                else:
                    # Asked for, but not RELIED on — see below.
                    rebuilt.append(b"Connection: close\r\n\r\n")
                writer.write(b"".join(rebuilt))
                await writer.drain()

            if method == "CONNECT" or is_upgrade:
                # A tunnel and an upgraded connection are both, by definition,
                # bidirectional and long-lived. The policy ran on the target
                # and the socket is pinned to the address it validated.
                await asyncio.gather(
                    _pipe(client_reader, writer),
                    _pipe(upstream_reader, client_writer),
                    return_exceptions=True,
                )
            else:
                # Forward exactly this request's body, then STOP reading the
                # client. `Connection: close` is a request to the ORIGIN, not
                # a guarantee: a client can pipeline, and an origin can ignore
                # it and hold the socket open. Piping the client direction
                # blindly meant a second request — for a different host, with
                # THAT host's cookies — was forwarded to the first origin with
                # no policy check. The danger was never reaching a new host;
                # it was the wrong host receiving another's credentials.
                body_len = _content_length(headers)
                if body_len:
                    with contextlib.suppress(Exception):
                        writer.write(
                            await asyncio.wait_for(
                                client_reader.readexactly(body_len),
                                timeout=_HEADER_TIMEOUT_S,
                            )
                        )
                        await writer.drain()
                with contextlib.suppress(Exception):
                    writer.write_eof()
                await _pipe(upstream_reader, client_writer)
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                client_writer.write(b"HTTP/1.1 408 Request Timeout\r\n\r\n")
                await client_writer.drain()
        except Exception:  # pylint: disable=broad-except
            log.exception("egress guard handler crashed")
        finally:
            if upstream_writer is not None:
                with contextlib.suppress(Exception):
                    upstream_writer.close()
            with contextlib.suppress(Exception):
                client_writer.close()

    return handle


@contextlib.asynccontextmanager
async def open_egress_guard(*, resolve: bool = True) -> AsyncIterator[EgressGuard]:
    """Run the guard for the life of the block. Yields the `EgressGuard`.

    Bound to 127.0.0.1 and never 0.0.0.0: an unauthenticated open proxy on the
    compose network would be a worse hole than the one this closes.
    """
    guard = EgressGuard("127.0.0.1", 0)
    server = await asyncio.start_server(
        _make_handler(guard, resolve=resolve),
        host="127.0.0.1",
        port=0,
        limit=_STREAM_LIMIT,
    )
    guard.port = server.sockets[0].getsockname()[1]
    log.debug("egress guard up: %s", guard.url)
    try:
        yield guard
    finally:
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()
        log.debug("egress guard closed: %s (denied %d)", guard.url, len(guard.denied))
