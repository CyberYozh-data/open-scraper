"""The measurement the whole landing check exists for, against real Chromium.

Playwright does NOT invoke a `context.route` handler for redirect hops, so the
route guard cannot see a 302 into the internal network. Only walking the chain
after `goto` catches it.

Deliberately NOT marked `e2e`: CI installs browsers, and this is the one test
that proves the redirect vector is closed. Marked e2e it would never run, and
the vector would be closed only in the commit message.

The first hop is allowlisted so that ONLY the redirect check can fail the
fetch — if the pre-flight were what turned this red, the test would prove
nothing about redirects.
"""
from __future__ import annotations

import contextlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.browser.runner import PlaywrightRunner
from src.security.egress import EGRESS_BLOCKED_ERROR
from src.settings import settings

INTERNAL_SECRET = "INTERNAL-SECRET-DO-NOT-RETURN"


@pytest.fixture(autouse=True)
def _transport_guard_on(monkeypatch):
    """These assert ZERO internal requests, which only the transport guard can
    deliver — the layers above it can refuse content but not prevent the
    fetch. The flag ships off, so the file that measures the difference turns
    it on explicitly."""
    monkeypatch.setattr(settings, "egress_transport_guard", True)


class _Internal(BaseHTTPRequestHandler):
    """Stands in for a service on the docker network. Counts its hits."""

    hits = 0
    paths: list = []

    def do_GET(self):  # noqa: N802 - stdlib API
        type(self).hits += 1
        type(self).paths.append(self.path)
        body = f"<html><body>{INTERNAL_SECRET}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _make_redirector(target: str):
    class _Redirector(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib API
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    return _Redirector


@contextlib.contextmanager
def _serving(handler, host: str):
    server = ThreadingHTTPServer((host, 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_public_url_redirecting_to_an_internal_host_returns_no_content(monkeypatch):
    _Internal.hits = 0
    _Internal.paths = []
    # 127.0.0.2 is loopback but a DIFFERENT address than the allowlisted entry,
    # so it stands in for "internal, and not the host we permitted".
    with _serving(_Internal, "127.0.0.2") as internal_port:
        target = f"http://127.0.0.2:{internal_port}/secret"
        with _serving(_make_redirector(target), "127.0.0.1") as entry_port:
            # Only the entry point is permitted. The redirect target is not.
            monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")

            runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=15000)
            try:
                result = await runner.fetch(
                    url=f"http://127.0.0.1:{entry_port}/start",
                    device="desktop",
                    proxy=None,
                    headers=None,
                    wait_until="domcontentloaded",
                    wait_for_selector=None,
                    timeout_ms=15000,
                    screenshot=False,
                )
            finally:
                await runner.stop()

    assert result.ok is False
    assert result.error == EGRESS_BLOCKED_ERROR
    assert INTERNAL_SECRET not in (result.html or "")
    # The acknowledged residual of the landing check: the document GET has
    # already fired by the time we can refuse its body. Exactly one — a retry
    # would mean the refusal is not terminal. The transport guard turns this
    # into zero, and that change is the measurable difference between the two
    # PRs, not a rewording of the same claim.
    assert _Internal.paths == [], f"paths={_Internal.paths}"


@pytest.mark.asyncio
async def test_favicon_to_an_internal_host_is_not_interceptable(monkeypatch):
    """A measured hole in the route-guard layer, recorded rather than hidden.

    Chromium issues `/favicon.ico` below the level Playwright's `route`
    handler sees, so the guard cannot abort it: after a redirect into an
    internal host the browser may still fetch a favicon from that host. It
    leaks nothing back to the caller (the fetch is already refused) but it IS
    an unguarded request to an internal address, and it is the concrete reason
    the route guard is not sufficient on its own.

    That hole is CLOSED by the transport guard: the favicon request goes
    through the proxy like everything else, so it is denied before a connection
    is made. This test is now a real assertion rather than documentation of a
    known gap — the internal host must record nothing at all.
    """
    _Internal.hits = 0
    _Internal.paths = []
    with _serving(_Internal, "127.0.0.2") as internal_port:
        target = f"http://127.0.0.2:{internal_port}/secret"
        with _serving(_make_redirector(target), "127.0.0.1") as entry_port:
            monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
            runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=15000)
            try:
                await runner.fetch(
                    url=f"http://127.0.0.1:{entry_port}/start",
                    device="desktop",
                    proxy=None,
                    headers=None,
                    wait_until="domcontentloaded",
                    wait_for_selector=None,
                    timeout_ms=15000,
                    screenshot=False,
                )
            finally:
                await runner.stop()

    assert _Internal.paths == [], f"paths={_Internal.paths}"


@pytest.mark.asyncio
async def test_allowlisted_host_is_still_reachable(monkeypatch):
    """Proves the previous test fails on the redirect and not on the entry
    point — without this, an over-broad guard would look like a pass."""
    with _serving(_Internal, "127.0.0.1") as port:
        monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=15000)
        try:
            result = await runner.fetch(
                url=f"http://127.0.0.1:{port}/direct",
                device="desktop",
                proxy=None,
                headers=None,
                wait_until="domcontentloaded",
                wait_for_selector=None,
                timeout_ms=15000,
                screenshot=False,
            )
        finally:
            await runner.stop()

    assert result.ok is True
    assert INTERNAL_SECRET in result.html


def _make_meta_refresh(target: str):
    """An entry page that navigates itself AFTER `goto` has already returned."""

    class _Entry(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib API
            body = (
                f'<html><head><meta http-equiv="refresh" content="0;url={target}">'
                f"</head><body>waiting</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    return _Entry


@pytest.mark.asyncio
async def test_late_navigation_into_an_internal_host_returns_no_content(monkeypatch):
    """The landing check is a SNAPSHOT taken right after `goto`.

    A page that keeps navigating lands somewhere that check never saw:
    `wait_for_selector` and `read_content_settling_navigation` both run before
    the body is read, and the latter's own docstring says some targets keep
    navigating after domcontentloaded. Measured before the fix — ok=True,
    final_url on the internal host, and the secret in `html`.

    Both public hops are allowlisted so the ONLY thing that can fail this test
    is the re-check at read time. Real shape needs no allowlist at all:
    `https://evil.example/` meta-refreshes to `https://evil.example/hop`, which
    302s to `http://10.0.2.1/admin`.
    """
    _Internal.hits = 0
    _Internal.paths = []
    with _serving(_Internal, "127.0.0.2") as internal_port:
        internal = f"http://127.0.0.2:{internal_port}/secret"
        with _serving(_make_redirector(internal), "127.0.0.1") as hop_port:
            hop = f"http://127.0.0.1:{hop_port}/hop"
            with _serving(_make_meta_refresh(hop), "127.0.0.1") as entry_port:
                monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
                runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=15000)
                try:
                    result = await runner.fetch(
                        url=f"http://127.0.0.1:{entry_port}/start",
                        device="desktop",
                        proxy=None,
                        headers=None,
                        wait_until="domcontentloaded",
                        wait_for_selector=None,
                        timeout_ms=15000,
                        screenshot=False,
                    )
                finally:
                    await runner.stop()

    assert INTERNAL_SECRET not in (result.html or ""), "internal body returned to the caller"
    assert result.ok is False
    assert result.error == EGRESS_BLOCKED_ERROR


class _Painter(BaseHTTPRequestHandler):
    """An internal page that paints a solid colour, so its disclosure can be
    measured in a screenshot even when same-origin rules keep it out of html."""

    hits: list = []

    def do_GET(self):  # noqa: N802 - stdlib API
        type(self).hits.append(self.path)
        body = (
            "<html><body style='margin:0;background:#ff00ff;width:100%;height:100%'>"
            f"{INTERNAL_SECRET}</body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _make_framer(target: str):
    class _Framer(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib API
            body = (
                "<html><body style='margin:0'>"
                f"<iframe src='{target}' style='width:800px;height:600px;border:0'>"
                "</iframe></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    return _Framer


def _magenta_pixels(b64: str) -> int:
    import base64
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    return sum(1 for px in image.getdata() if px == (255, 0, 255))


@pytest.mark.asyncio
async def test_an_iframe_redirecting_internal_does_not_reach_the_screenshot(monkeypatch):
    """`page.url` is the MAIN frame. A sub-frame pointed at a public URL that
    302s internal is invisible to every layer: the route guard is not invoked
    for redirect hops, and the read-time check only ever looked at the main
    frame. `page.content()` is same-origin-protected so the secret never shows
    up in `html` — but the rendered pixels come back in `screenshot_b64`, which
    is the same disclosure.
    """
    _Painter.hits = []
    with _serving(_Painter, "127.0.0.2") as internal_port:
        internal = f"http://127.0.0.2:{internal_port}/secret"
        with _serving(_make_redirector(internal), "127.0.0.1") as hop_port:
            hop = f"http://127.0.0.1:{hop_port}/hop"
            with _serving(_make_framer(hop), "127.0.0.1") as entry_port:
                monkeypatch.setattr(settings, "egress_allow_hosts", "127.0.0.1")
                runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=15000)
                try:
                    result = await runner.fetch(
                        url=f"http://127.0.0.1:{entry_port}/start",
                        device="desktop",
                        proxy=None,
                        headers=None,
                        wait_until="load",
                        wait_for_selector=None,
                        timeout_ms=15000,
                        screenshot=True,
                    )
                finally:
                    await runner.stop()

    painted = _magenta_pixels(result.screenshot_b64) if result.screenshot_b64 else 0
    assert painted == 0, f"{painted} pixels of the internal page reached the caller"
    assert result.ok is False
