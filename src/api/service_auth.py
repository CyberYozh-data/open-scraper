from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException, Request

from src.settings import settings

log = logging.getLogger(__name__)

# The path is caller-controlled and percent-decoded by the time it reaches
# `request.url.path`, so `/api/v1/sessions/{id}` can carry terminal escapes or
# Unicode line separators into a plain-text log record. `%r` escapes them and
# the cap stops one request burying the rest of the line. (The same fix was
# made on the yozh side earlier and not carried here — hence this comment.)
_MAX_LOGGED_PATH = 120


def _safe_path(request: Request) -> str:
    return request.url.path[:_MAX_LOGGED_PATH]


def _client_host(request: Request) -> str:
    """Just the peer host — enough to tell one caller from another.

    No port and no headers: an `X-Forwarded-For` here would be
    attacker-controlled text in an operator's log.
    """
    return request.client.host if request.client else "unknown"

SERVICE_TOKEN_HEADER = "X-Service-Token"


async def require_service_token(
    request: Request,
    x_service_token: str | None = Header(default=None, alias=SERVICE_TOKEN_HEADER),
) -> None:
    """Fail-closed guard for endpoints that hand out proxy credentials.

    Three surfaces now: ``GET /api/v1/proxies/resolve`` (CRIT-01 — returns an
    upstream proxy URL with embedded, reusable username/password), the
    ``/api/v1/sessions`` router (CRIT-02 — session ids are hijackable), and the
    ``/api/v2/prem-proxies`` catalog (paid-account metadata plus an uncapped
    relay to the vendor on this account's API key). Any client that reaches the
    listener — the host-published port, or any container on
    ``open-scraper-net`` — could otherwise read all three anonymously.

    Legitimate callers are same-trust-domain services (the crawler's ``/map``)
    that present a shared ``SERVICE_TOKEN`` as the ``X-Service-Token`` header.

    - No token configured -> 503: refuse to serve credentials at all rather
      than fall back to an open endpoint.
    - Token configured but header missing/wrong -> 401.

    The comparison is constant-time to avoid leaking the token via timing.
    """
    configured = settings.service_token
    secret = configured.get_secret_value() if configured is not None else ""
    if not secret:
        # Logged because the whole risk of gating a surface is a caller that
        # can no longer reach it — and every caller here SWALLOWS the failure:
        # the law-checker turns it into one warning and a proxy-less scan, the
        # admin passthrough into an empty panel. Without a line on this side
        # there is nothing to correlate against. Never the header value: that
        # is the secret, or a hostile caller's guess at it.
        log.warning(
            "service auth refused (503, no SERVICE_TOKEN configured): %r from %s",
            _safe_path(request), _client_host(request),
            extra={"event": "auth.service_token.unconfigured"},
        )
        raise HTTPException(
            status_code=503,
            detail="service auth not configured: set SERVICE_TOKEN to enable this endpoint",
        )
    # Compare as bytes: hmac.compare_digest rejects str with non-ASCII, which a
    # hostile caller could send to turn a 401 into a 500.
    if x_service_token is None or not hmac.compare_digest(
        x_service_token.encode("utf-8", "ignore"), secret.encode("utf-8")
    ):
        log.warning(
            "service auth refused (401, %s token): %r from %s",
            "missing" if x_service_token is None else "wrong",
            _safe_path(request), _client_host(request),
            extra={"event": "auth.service_token.refused"},
        )
        raise HTTPException(status_code=401, detail="invalid or missing service token")
