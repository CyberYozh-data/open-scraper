"""Closing what a run opened — once, so the two copies cannot drift again.

There were two of these: `PlaywrightRunner.fetch`'s `_teardown` and the login
task's `finally`. The first got a log line and a deadline; the second was
missed and stayed three silent `except Exception: pass` blocks — on the path
that holds an authenticated browser session. The original report named it and
it was dropped while disproving two lines that sat near it. One implementation
is the fix for that class of miss, not just for this instance.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

# A wedged-but-alive browser makes `close()` hang forever, and teardown runs
# from a `finally`, so without a deadline the caller never returns and nothing
# is logged. Generous: a healthy close is milliseconds.
TEARDOWN_TIMEOUT_S = 10.0


async def close_quietly(
    what: str,
    close: Callable[[], Awaitable[object]],
    *,
    owner: str | None = None,
    timeout: float = TEARDOWN_TIMEOUT_S,
) -> None:
    """Close one resource. Never raises, never hangs, never silent on failure.

    Non-fatal by design — a teardown fault must not turn work that already
    succeeded into an error — but no longer invisible. Measured on Playwright
    1.57: closing a page or context whose browser has already died is a SILENT
    NO-OP, including after SIGKILL to every Chrome process, so a crashed
    browser was never the case that needed a log line. What bites is a browser
    that is wedged but ALIVE, where `close()` simply never returns.

    `owner` is whatever names the work — a URL, a session id — because
    "page did not close" gives a count, not a culprit.
    """
    try:
        await asyncio.wait_for(close(), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning(
            "teardown: %s did not close within %.0fs for %s",
            what, timeout, owner or "?",
            extra={"event": "browser.teardown.timeout"},
        )
    except Exception as exc:  # pylint: disable=broad-except
        log.warning(
            "teardown: %s did not close for %s (%s)",
            what, owner or "?", exc,
            extra={"event": "browser.teardown.failed"},
        )
