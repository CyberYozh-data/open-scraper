from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


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
            log.debug("CyberYozh got response %s", response.text)
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
        log.debug("POST /proxies/rotating-credentials/ with payload: %s", payload)

        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.post(
                f"{self._base_url}/proxies/rotating-credentials/",
                headers=self._headers(),
                json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise httpx.HTTPStatusError(
                    f"{e} | response_body={response.text[:1000]} | payload={payload}",
                    request=e.request,
                    response=e.response,
                ) from e

            response_data = response.json()

        log.debug("rotating_credentials response: %s", response_data)

        creds = response_data.get("credentials") or []
        if isinstance(creds, list):
            return [str(x) for x in creds]
        return [str(creds)]

    async def call_change_ip_link(self, url: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
