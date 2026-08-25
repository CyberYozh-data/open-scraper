from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from src.utils.redaction import redact_mapping

log = logging.getLogger(__name__)

# Keys in a provider request body that must never travel into a log line or an
# exception message — the latter becomes an HTTP 502 detail and a job's
# caller-visible error.
_SECRET_PAYLOAD_KEYS = frozenset({"connection_login", "connection_password"})


@dataclass
class OrderedProxy:
    id: str
    url: str
    login: str
    password: str
    status: str
    expired: bool
    change_ip_links: list[str]
    connection_host: str | None = None
    connection_port: int | None = None
    access_type: str | None = None  # "private" or "shared"


class CyberYozhClient:
    """
    Client for CyberYozh API.

    Wait base_url like:
      https://app.cyberyozh.com/api/v1
    """

    def __init__(self, base_url: str, api_key: str, timeout_s: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "X-Api-Key": self._api_key,
        }

    async def proxy_history(self, *, category: str, expired: bool = False) -> list[OrderedProxy]:
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.get(
                f"{self._base_url}/proxies/history/",
                headers=self._headers(),
                params={"category": category, "expired": str(expired).lower()},
            )
            # Was `log.debug(..., response.text)`: that body carries a
            # credentialed URL per item. The status is what the line was ever
            # useful for.
            log.debug("CyberYozh proxy history responded %s", response.status_code)
            response.raise_for_status()
            data: Any = response.json()

        items: list[dict[str, Any]]
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and isinstance(data.get("results"), list):
            items = data["results"]
        else:
            raise RuntimeError(f"Unexpected /proxies/history response shape: {type(data)}")

        proxies: list[OrderedProxy] = []
        for item in items:
            proxies.append(
                OrderedProxy(
                    id=str(item.get("id")),
                    url=str(item.get("url") or ""),
                    login=str(item.get("connection_login") or ""),
                    password=str(item.get("connection_password") or ""),
                    status=str(item.get("system_status") or ""),
                    expired=bool(item.get("expired")),
                    change_ip_links=list(item.get("change_ip_links") or []),
                    connection_host=item.get("connection_host"),
                    connection_port=item.get("connection_port"),
                    access_type=item.get("access_type"),
                )
            )
        return proxies

    async def rotating_credentials(self, payload: dict[str, Any]) -> list[str]:
        log.debug(
            "POST /proxies/rotating-credentials/ with payload: %s",
            redact_mapping(payload, secret_keys=_SECRET_PAYLOAD_KEYS),
        )

        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.post(
                f"{self._base_url}/proxies/rotating-credentials/",
                headers=self._headers(),
                json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                # The payload carries connection_login/connection_password, and
                # this message does not stay here: provider.py re-raises it as a
                # RuntimeError which becomes an HTTP 502 `detail` and a scrape
                # job's caller-visible `error` + `warnings`. The response body
                # is the provider's, not ours, but it is bounded for the same
                # reason it always was.
                raise httpx.HTTPStatusError(
                    f"{e} | response_body={response.text[:1000]} "
                    f"| payload={redact_mapping(payload, secret_keys=_SECRET_PAYLOAD_KEYS)}",
                    request=e.request,
                    response=e.response,
                ) from e

            response_data = response.json()

        creds = response_data.get("credentials") or []
        # NOT the response body: `credentials` is a list of `user:pass@host`
        # strings, so this line put the proxy password in the log on the SUCCESS
        # path — the one place a sweep for error handling does not look. Caught
        # in review after the first pass missed it.
        log.debug(
            "rotating_credentials returned %d credentials",
            len(creds) if isinstance(creds, list) else 1,
        )
        if isinstance(creds, list):
            return [str(x) for x in creds]
        return [str(creds)]

    async def call_change_ip_link(self, url: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
