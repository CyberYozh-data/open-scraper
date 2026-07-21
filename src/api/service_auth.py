from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from src.settings import settings

SERVICE_TOKEN_HEADER = "X-Service-Token"


async def require_service_token(
    x_service_token: str | None = Header(default=None, alias=SERVICE_TOKEN_HEADER),
) -> None:
    """Fail-closed guard for endpoints that hand out proxy credentials.

    CRIT-01: ``GET /api/v1/proxies/resolve`` returns an upstream proxy URL with
    embedded, reusable username/password. Any client that reaches the listener
    (the host-published port or any container on ``open-scraper-net``) could
    otherwise pull paid proxy credentials anonymously.

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
        raise HTTPException(
            status_code=503,
            detail="service auth not configured: set SERVICE_TOKEN to enable this endpoint",
        )
    # Compare as bytes: hmac.compare_digest rejects str with non-ASCII, which a
    # hostile caller could send to turn a 401 into a 500.
    if x_service_token is None or not hmac.compare_digest(
        x_service_token.encode("utf-8", "ignore"), secret.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid or missing service token")
