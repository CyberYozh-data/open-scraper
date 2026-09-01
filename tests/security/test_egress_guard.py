"""The transport guard: the layer that PREVENTS the request.

Every layer above it refuses content after the fact. Measured against real
Chromium: a proxy in the context's `proxy=` slot is handed BOTH hops of a
redirect (`GET http://entry/start` then `GET http://internal/final`), and it is
handed a HOSTNAME (`CONNECT example.com:443`), not an address the browser has
already resolved. So this layer sees what the route guard cannot, and — because
it resolves the name itself and then dials the address it validated — closes
the TOCTOU window the other layers cannot.

No browser is needed to test any of this: it is a proxy, so it can be driven
with a socket.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

import pytest

from src.security.egress import EGRESS_BLOCKED_ERROR
from src.security.egress_guard import open_egress_guard
from src.settings import settings

BODY = "GUARDED-TARGET-BODY"


class _Origin(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # noqa: N802 - stdlib API
        type(self).hits += 1
        payload = f"<html><body>{BODY}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def _serving(host: str = "127.0.0.1"):
    _Origin.hits = 0
    server = ThreadingHTTPServer((host, 0), _Origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


async def _speak(guard_url: str, request: str) -> bytes:
    """Send one raw request to the guard and read the whole reply."""
    host, port = guard_url.removeprefix("http://").split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(request.encode())
    await writer.drain()
    try:
        # Read to EOF, not one chunk: headers and body arrive separately and a
        # single read makes the body look absent.
        data = await asyncio.wait_for(reader.read(), timeout=10)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    return data


@pytest.mark.asyncio
async def test_relays_a_permitted_target(monkeypatch):
    monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
    with _serving() as port:
        async with open_egress_guard(resolve=True) as guard:
            reply = await _speak(
                guard.url,
                f"GET http://127.0.0.1:{port}/page HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n\r\n",
            )
    assert b"200" in reply.split(b"\r\n")[0]
    assert BODY.encode() in reply
    assert _Origin.hits == 1
    assert guard.denied == []


@pytest.mark.asyncio
async def test_refused_target_is_never_dialled():
    """Not 'the body was withheld' — the origin must record ZERO requests.
    That number is the whole difference between this layer and the ones above
    it, so it is what the test asserts."""
    with _serving("127.0.0.2") as port:
        async with open_egress_guard(resolve=True) as guard:
            reply = await _speak(
                guard.url,
                f"GET http://127.0.0.2:{port}/page HTTP/1.1\r\n"
                f"Host: 127.0.0.2:{port}\r\n\r\n",
            )
    assert b"403" in reply.split(b"\r\n")[0]
    assert BODY.encode() not in reply
    assert _Origin.hits == 0
    assert guard.denied == [f"127.0.0.2:{port}"]


@pytest.mark.asyncio
async def test_refused_connect_is_never_dialled():
    with _serving("127.0.0.2") as port:
        async with open_egress_guard(resolve=True) as guard:
            reply = await _speak(
                guard.url, f"CONNECT 127.0.0.2:{port} HTTP/1.1\r\n\r\n"
            )
    assert b"403" in reply.split(b"\r\n")[0]
    assert _Origin.hits == 0
    assert guard.denied == [f"127.0.0.2:{port}"]


@pytest.mark.asyncio
async def test_dials_the_address_it_validated_not_the_name(monkeypatch):
    """The TOCTOU test.

    The predicate resolves the hostname; the guard must then connect to THAT
    address. Handing the name to the connector would let a second lookup —
    a rebinding attack, or plain round-robin — reach an address nobody checked.
    """
    # No getaddrinfo patch here on purpose: the autouse `_no_live_dns` fixture
    # in conftest already answers 93.184.216.34 through the module-local seam.
    # A `src.security.egress.socket.getaddrinfo` patch would be a no-op for
    # this module AND would mutate the stdlib socket for every test running
    # alongside — the exact anti-pattern egress.py documents against.
    dialled = []
    real_open_connection = asyncio.open_connection

    async def _spy_open_connection(host=None, port=None, **kw):
        # `asyncio.open_connection` is module-global, so this fake also sees the
        # test's own connection TO the guard. Record only the guard's outbound
        # dial and let everything else through, or the helper below deadlocks.
        if host != "127.0.0.1":
            dialled.append((host, port))
            raise ConnectionRefusedError("probe only")
        return await real_open_connection(host, port, **kw)

    monkeypatch.setattr(asyncio, "open_connection", _spy_open_connection)
    async with open_egress_guard(resolve=True) as guard:
        await _speak(
            guard.url,
            "GET http://example.com/page HTTP/1.1\r\nHost: example.com\r\n\r\n",
        )
    assert dialled == [("93.184.216.34", 80)]


@pytest.mark.asyncio
async def test_binds_loopback_only():
    """An unauthenticated open proxy on the compose network would be a worse
    hole than the one this closes."""
    async with open_egress_guard(resolve=True) as guard:
        assert guard.url.startswith("http://127.0.0.1:")
        assert guard.host == "127.0.0.1"


@pytest.mark.asyncio
async def test_an_origin_form_request_is_rejected_whatever_the_method():
    """`TRACE` is a perfectly valid method token — the method is no longer what
    disqualifies this. What does is the ORIGIN-form target: a proxy is only
    ever sent absolute-form or CONNECT, so origin-form means something is
    talking to this listener that should not be."""
    async with open_egress_guard(resolve=True) as guard:
        reply = await _speak(guard.url, "TRACE / HTTP/1.1\r\n\r\n")
    assert b"400" in reply.split(b"\r\n")[0]
    assert guard.denied == []


@pytest.mark.asyncio
async def test_a_truncated_request_line_is_rejected():
    async with open_egress_guard(resolve=True) as guard:
        reply = await _speak(guard.url, "GET\r\n\r\n")
    assert b"405" in reply.split(b"\r\n")[0]


@pytest.mark.asyncio
async def test_a_non_absolute_get_is_rejected():
    """A proxy is only ever sent absolute-form or CONNECT. Origin-form means
    something is talking to this listener that should not be."""
    async with open_egress_guard(resolve=True) as guard:
        reply = await _speak(guard.url, "GET /page HTTP/1.1\r\nHost: x\r\n\r\n")
    assert b"400" in reply.split(b"\r\n")[0]


@pytest.mark.asyncio
async def test_the_listener_is_gone_after_teardown():
    async with open_egress_guard(resolve=True) as guard:
        url = guard.url
    host, port = url.removeprefix("http://").split(":")
    with pytest.raises((ConnectionRefusedError, OSError)):
        reader, writer = await asyncio.open_connection(host, int(port))
        writer.close()


@pytest.mark.asyncio
async def test_denials_are_deduplicated_and_bounded():
    """`denied` is surfaced to the caller as a warning; a hostile page with
    thousands of internal sub-resources must not turn it into a payload."""
    with _serving("127.0.0.2") as port:
        async with open_egress_guard(resolve=True) as guard:
            for _ in range(5):
                await _speak(
                    guard.url,
                    f"GET http://127.0.0.2:{port}/a HTTP/1.1\r\n"
                    f"Host: 127.0.0.2:{port}\r\n\r\n",
                )
    assert guard.denied == [f"127.0.0.2:{port}"]


@pytest.mark.asyncio
async def test_the_error_body_carries_the_constant_not_the_host():
    with _serving("127.0.0.2") as port:
        async with open_egress_guard(resolve=True) as guard:
            reply = await _speak(
                guard.url,
                f"GET http://127.0.0.2:{port}/x HTTP/1.1\r\n"
                f"Host: 127.0.0.2:{port}\r\n\r\n",
            )
    assert EGRESS_BLOCKED_ERROR.encode() in reply
    assert b"127.0.0.2" not in reply


@pytest.mark.asyncio
async def test_a_long_but_ordinary_request_still_works():
    """The regression that mattered. `asyncio.start_server` defaults its
    StreamReader to 64 KiB and `readline` RAISES past it — so a long SERP url
    or a fat cookie jar, both ordinary scraper traffic, returned zero bytes
    with a stack trace naming no target and nothing in `denied` to explain it.

    Asserted against a REFUSED target so the reply proves the request was
    fully parsed and reached the policy, not merely that it did not crash.
    """
    with _serving("127.0.0.2") as port:
        async with open_egress_guard(resolve=True) as guard:
            reply = await _speak(
                guard.url,
                f"GET http://127.0.0.2:{port}/?q=" + "A" * 70_000 + " HTTP/1.1\r\n"
                f"Host: 127.0.0.2:{port}\r\n"
                "Cookie: " + "B" * 70_000 + "\r\n\r\n",
            )
    assert b"403" in reply.split(b"\r\n")[0], reply[:120]
    assert _Origin.hits == 0


@pytest.mark.asyncio
async def test_a_request_line_past_the_limit_is_refused_cleanly():
    """Past the bound there still has to be an answer, not a crash."""
    from src.security.egress_guard import _STREAM_LIMIT

    async with open_egress_guard(resolve=True) as guard:
        reply = await _speak(
            guard.url,
            "GET http://example.com/?q=" + "A" * (_STREAM_LIMIT + 1000) + " HTTP/1.1\r\n"
            "Host: example.com\r\n\r\n",
        )
    assert reply.split(b"\r\n")[0].split()[1] in (b"414", b"431"), reply[:80]


@pytest.mark.asyncio
async def test_a_header_block_past_the_limit_is_refused_cleanly():
    from src.security.egress_guard import _MAX_HEADER_BYTES

    header = "X-Pad: " + "B" * 60_000 + "\r\n"
    count = (_MAX_HEADER_BYTES // len(header)) + 2
    async with open_egress_guard(resolve=True) as guard:
        reply = await _speak(
            guard.url,
            "GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n"
            + header * count
            + "\r\n",
        )
    assert reply.split(b"\r\n")[0].split()[1] in (b"414", b"431"), reply[:80]


@pytest.mark.parametrize("target", ["example.com:99999", "example.com:-1", "example.com:0"])
@pytest.mark.asyncio
async def test_an_out_of_range_port_is_refused_not_crashed(target):
    """`int(port_str)` was unvalidated: 99999 reached `open_connection` and
    raised OverflowError, which is not in the dial except-clause."""
    async with open_egress_guard(resolve=True) as guard:
        reply = await _speak(guard.url, f"CONNECT {target} HTTP/1.1\r\n\r\n")
    assert reply.split(b"\r\n")[0].split()[1] == b"400", reply[:80]


@pytest.mark.asyncio
async def test_every_resolved_address_is_tried_before_giving_up(monkeypatch):
    """`addrs[0]` alone drops multi-address failover: a host whose first
    address is unreachable 502s even though the second answers. Trying each in
    turn keeps the TOCTOU property — every candidate was validated."""
    monkeypatch.setattr(
        "src.security.egress._getaddrinfo",
        lambda *a, **k: [
            (0, 0, 0, "", ("93.184.216.34", 0)),
            (0, 0, 0, "", ("93.184.216.35", 0)),
        ],
    )
    dialled = []
    real_open = asyncio.open_connection

    async def _spy(host=None, port=None, **kw):
        if host == "127.0.0.1":
            return await real_open(host, port, **kw)
        dialled.append(host)
        if host == "93.184.216.34":
            raise ConnectionRefusedError("first address down")
        raise ConnectionRefusedError("probe stop")

    monkeypatch.setattr(asyncio, "open_connection", _spy)
    async with open_egress_guard(resolve=True) as guard:
        await _speak(
            guard.url, "GET http://example.com/x HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
    assert dialled == ["93.184.216.34", "93.184.216.35"], dialled


@pytest.mark.asyncio
async def test_a_websocket_upgrade_survives_the_guard(monkeypatch):
    """`Connection: close` was applied to every forwarded request, which strips
    the `Upgrade` a plain `ws://` handshake needs — so a legitimate websocket
    on the direct path broke the moment the guard was on. Raised on review.

    The close is still right for ordinary requests (a kept-alive second request
    on the same socket would skip the check), but an upgrade is by definition a
    connection that continues, so the two cannot share one rule.
    """
    monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
    seen: dict = {}

    async def _origin(reader, writer):
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        seen["headers"] = raw.decode(errors="replace")
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_origin, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with open_egress_guard(resolve=True) as guard:
            reply = await _speak(
                guard.url,
                f"GET http://127.0.0.1:{port}/ws HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n",
            )
    finally:
        server.close()
        await server.wait_closed()

    forwarded = seen.get("headers", "").lower()
    assert "upgrade: websocket" in forwarded, seen.get("headers")
    assert "connection: close" not in forwarded, seen.get("headers")
    assert b"101" in reply.split(b"\r\n")[0], reply[:80]


@pytest.mark.asyncio
async def test_an_ordinary_request_still_gets_connection_close(monkeypatch):
    """The upgrade exemption must not weaken the rule it carves out of: an
    ordinary forwarded request still gets one request per connection, or a
    pipelined second one would skip the policy check."""
    monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
    seen: dict = {}

    async def _origin(reader, writer):
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        seen["headers"] = raw.decode(errors="replace")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_origin, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with open_egress_guard(resolve=True) as guard:
            await _speak(
                guard.url,
                f"GET http://127.0.0.1:{port}/page HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n\r\n",
            )
    finally:
        server.close()
        await server.wait_closed()

    assert "connection: close" in seen.get("headers", "").lower()


@pytest.mark.asyncio
async def test_an_upgrade_to_a_refused_target_is_still_blocked(monkeypatch):
    """The exemption is about framing, never about policy."""
    with _serving("127.0.0.2") as port:
        async with open_egress_guard(resolve=True) as guard:
            reply = await _speak(
                guard.url,
                f"GET http://127.0.0.2:{port}/ws HTTP/1.1\r\n"
                f"Host: 127.0.0.2:{port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n",
            )
    assert b"403" in reply.split(b"\r\n")[0]
    assert _Origin.hits == 0


@pytest.mark.asyncio
async def test_a_pipelined_second_request_is_never_forwarded(monkeypatch):
    """`Connection: close` is a REQUEST to the origin, not a guarantee.

    Everything after the first header block went straight into `_pipe`, so a
    client that pipelines — or an origin that ignores the close and keeps the
    socket alive — could get a request meant for host B, carrying B's cookies,
    delivered to host A, with no policy check on the second one. The danger is
    not reaching a new host; it is the WRONG host receiving the credentials
    for another. Found by the codex second pass.
    """
    monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
    seen: list = []

    async def _origin(reader, writer):
        try:
            while True:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
                seen.append(raw.decode(errors="replace").splitlines()[0])
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
        except Exception:
            pass

    server = await asyncio.start_server(_origin, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with open_egress_guard(resolve=True) as guard:
            host, gport = guard.url.removeprefix("http://").split(":")
            reader, writer = await asyncio.open_connection(host, int(gport))
            # Both requests in one write: the second must not reach the origin.
            writer.write(
                f"GET http://127.0.0.1:{port}/first HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n\r\n"
                f"GET http://127.0.0.2:{port}/second HTTP/1.1\r\n"
                f"Host: 127.0.0.2:{port}\r\nCookie: SECRET=1\r\n\r\n".encode()
            )
            await writer.drain()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    assert len(seen) == 1, f"a second request reached the origin: {seen}"
    assert "/first" in seen[0]


@pytest.mark.asyncio
async def test_a_request_body_is_still_forwarded(monkeypatch):
    """Bounding the client direction must not break a POST."""
    monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
    got: dict = {}

    async def _origin(reader, writer):
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        length = 0
        for line in head.decode(errors="replace").splitlines():
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1])
        got["body"] = (await asyncio.wait_for(reader.readexactly(length), timeout=5)).decode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_origin, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with open_egress_guard(resolve=True) as guard:
            await _speak(
                guard.url,
                f"POST http://127.0.0.1:{port}/submit HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\nContent-Length: 9\r\n\r\nbody=here",
            )
    finally:
        server.close()
        await server.wait_closed()

    assert got.get("body") == "body=here"


@pytest.mark.asyncio
async def test_all_addresses_share_one_dial_deadline(monkeypatch):
    """Multi-address failover was added to fix an earlier finding — and gave
    EACH address a fresh 30s. The navigation deadline is also 30s, so the
    browser gives up before the working address is ever tried, and handler
    tasks keep dialling N x 30s after the page is dead."""
    from src.security import egress_guard

    monkeypatch.setattr(egress_guard, "_DIAL_TIMEOUT_S", 0.3)
    monkeypatch.setattr(
        "src.security.egress._getaddrinfo",
        lambda *a, **k: [(0, 0, 0, "", (f"93.184.216.{i}", 0)) for i in range(1, 6)],
    )
    real_open = asyncio.open_connection

    async def _slow(host=None, port=None, **kw):
        if host == "127.0.0.1":
            return await real_open(host, port, **kw)
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio, "open_connection", _slow)
    started = time.perf_counter()
    async with open_egress_guard(resolve=True) as guard:
        await _speak(
            guard.url, "GET http://example.com/x HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
    elapsed = time.perf_counter() - started
    # One budget for the whole dial, not 5 x 0.3s.
    assert elapsed < 1.2, f"took {elapsed:.1f}s — each address got its own deadline"


@pytest.mark.parametrize("method", ["PROPFIND", "REPORT", "COPY", "MOVE"])
@pytest.mark.asyncio
async def test_a_valid_extension_method_is_forwarded(method, monkeypatch):
    """Browser Fetch permits extension methods after preflight, and the guard
    is on by default now — an allowlist of common verbs silently breaks WebDAV
    and custom HTTP that worked on the direct path before."""
    monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
    seen: dict = {}

    async def _origin(reader, writer):
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        seen["line"] = raw.decode(errors="replace").splitlines()[0]
        writer.write(b"HTTP/1.1 207 Multi-Status\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_origin, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with open_egress_guard(resolve=True) as guard:
            await _speak(
                guard.url,
                f"{method} http://127.0.0.1:{port}/dav HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n\r\n",
            )
    finally:
        server.close()
        await server.wait_closed()

    assert seen.get("line", "").startswith(method), seen


@pytest.mark.asyncio
async def test_a_garbage_method_is_still_refused():
    async with open_egress_guard(resolve=True) as guard:
        reply = await _speak(guard.url, "GET\x00BAD / HTTP/1.1\r\n\r\n")
    assert reply.split(b"\r\n")[0].split()[1] in (b"400", b"405"), reply[:80]


@pytest.mark.asyncio
async def test_each_recorded_target_is_truncated():
    """`_MAX_DENIED` caps the NUMBER of entries, not the size of each. A
    request line may approach the stream limit, so twenty refusals could
    become a multi-megabyte payload on the way to `warnings`."""
    from src.security import egress_guard

    long_host = "a" * 5000 + ".example"
    async with open_egress_guard(resolve=True) as guard:
        await _speak(
            guard.url,
            f"GET http://{long_host}/x HTTP/1.1\r\nHost: {long_host}\r\n\r\n",
        )
    assert guard.denied, "the refusal was not recorded at all"
    for entry in guard.denied:
        assert len(entry) <= egress_guard.MAX_DENIED_TARGET_CHARS, len(entry)
