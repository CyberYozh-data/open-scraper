from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx


log = logging.getLogger(__name__)


class ScraperError(Exception):
    def __init__(self, *, status_code: int | None, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.retryable = retryable


class ScraperClient:
    """Async HTTP client to the open-scraper service.

    Submits a scrape job, polls until done, returns the single ScrapeResponse.
    The scraper always returns a list (even for POST /scrape/page — job holds
    one page); we unwrap results[0] for the caller.
    """

    def __init__(
        self,
        base_url: str,
        poll_interval_ms: int,
        timeout_ms: int,
        service_token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._poll = poll_interval_ms / 1000.0
        self._timeout_s = timeout_ms / 1000.0
        # The scraper gates its credential-bearing /proxies/resolve endpoint on a
        # shared SERVICE_TOKEN (X-Service-Token). Send it on every call so /map's
        # proxy resolution is authorised; harmless on the ungated /scrape routes.
        headers = {"X-Service-Token": service_token} if service_token else None
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout_s),
            headers=headers,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            r = await self._client.get("/api/v1/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def resolve_proxy(
        self,
        proxy_type: str,
        proxy_pool_id: str | None = None,
        proxy_geo: dict[str, str] | None = None,
        prem_proxy_options: dict | None = None,
    ) -> str | None:
        """Ask the scraper to resolve an upstream proxy URL (reuses its CyberYozh
        resolution). Returns the SOCKS5 URL, or None for proxy_type=none. A real
        proxy_type the scraper can't provide (e.g. no key configured) comes back
        as a non-200 and raises ScraperError — it never returns None as "direct"."""
        params: dict[str, str] = {"proxy_type": proxy_type}
        if proxy_pool_id:
            params["proxy_pool_id"] = proxy_pool_id
        for key in ("country_code", "region", "city"):
            value = (proxy_geo or {}).get(key)
            if value:
                params[key] = value
        # prem_res_rotating targeting → flat query params the scraper's /resolve
        # accepts. `zip` is sent as `zip_code` (the helper's param name).
        for src_key, param in (
            ("ip_filter", "ip_filter"), ("isp", "isp"), ("zip", "zip_code"),
            ("session_type", "session_type"), ("sticky_id", "sticky_id"),
            ("rotation_minutes", "rotation_minutes"), ("sub_user_id", "sub_user_id"),
            ("protocol", "protocol"),
        ):
            value = (prem_proxy_options or {}).get(src_key)
            if value is not None:
                params[param] = str(value)
        try:
            r = await self._client.get("/api/v1/proxies/resolve", params=params)
        except httpx.HTTPError as e:
            raise ScraperError(
                status_code=None, message=f"resolve_proxy unreachable: {e}", retryable=True
            ) from e
        if r.status_code != 200:
            raise ScraperError(
                status_code=r.status_code,
                message=f"resolve_proxy returned {r.status_code}: {r.text[:200]}",
                retryable=False,
            )
        return r.json().get("proxy_url")

    async def fetch(self, url: str, scrape_options: dict[str, Any]) -> dict[str, Any]:
        body = {"url": url, **scrape_options}

        try:
            create = await self._client.post("/api/v1/scrape/page", json=body)
        except httpx.HTTPError as e:
            raise ScraperError(status_code=None, message=f"scraper unreachable: {e}", retryable=True) from e

        if create.status_code >= 500:
            raise ScraperError(
                status_code=create.status_code,
                message=f"scraper 5xx on submit: {create.text[:200]}",
                retryable=True,
            )
        if create.status_code >= 400:
            raise ScraperError(
                status_code=create.status_code,
                message=f"scraper rejected request: {create.text[:400]}",
                retryable=False,
            )

        job_id = create.json().get("job_id")
        if not job_id:
            raise ScraperError(status_code=None, message="scraper returned no job_id", retryable=False)

        deadline = asyncio.get_running_loop().time() + self._timeout_s
        while True:
            if asyncio.get_running_loop().time() > deadline:
                raise ScraperError(
                    status_code=None,
                    message=f"scraper job {job_id} polling deadline exceeded",
                    retryable=True,
                )

            try:
                st = await self._client.get(f"/api/v1/scrape/{job_id}")
            except httpx.HTTPError as e:
                raise ScraperError(status_code=None, message=f"poll failed: {e}", retryable=True) from e

            if st.status_code != 200:
                raise ScraperError(
                    status_code=st.status_code,
                    message=f"poll returned {st.status_code}: {st.text[:200]}",
                    retryable=True,
                )

            data = st.json()
            status = data.get("status")
            if status in ("queued", "running"):
                await asyncio.sleep(self._poll)
                continue
            if status == "failed":
                err = data.get("error") or "unknown"
                raise ScraperError(status_code=None, message=f"scraper job failed: {err}", retryable=True)
            if status == "done":
                break
            raise ScraperError(status_code=None, message=f"unexpected status: {status}", retryable=False)

        try:
            res = await self._client.get(f"/api/v1/scrape/{job_id}/results")
        except httpx.HTTPError as e:
            raise ScraperError(status_code=None, message=f"results fetch failed: {e}", retryable=True) from e

        if res.status_code != 200:
            raise ScraperError(
                status_code=res.status_code,
                message=f"results endpoint returned {res.status_code}",
                retryable=True,
            )
        payload = res.json()
        results = payload.get("results") or []
        if not results:
            raise ScraperError(
                status_code=None,
                message="scraper returned no results — possibly cancelled",
                retryable=True,
            )
        return results[0]
