from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class CyberYozhV2Client:
    """Client for the CyberYozh v2 premium rotating-proxy API.

    base_url must already include the version path, e.g.
      https://app.cyberyozh.com/api/v2/rotating-proxies
    Auth is the same X-Api-Key as the v1 client.
    """

    def __init__(self, base_url: str, api_key: str, timeout_s: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        return {"accept": "application/json", "X-Api-Key": self._api_key}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.get(
                f"{self._base_url}{path}", headers=self._headers(), params=params or {}
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _unwrap(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            return data["results"]
        raise RuntimeError(f"unexpected list response shape: {type(data)}")

    async def subscription(self) -> dict[str, Any]:
        return await self._get("/subscription/")

    async def session_options(self) -> dict[str, Any]:
        return await self._get("/session-options/")

    async def sub_users(self) -> list[dict[str, Any]]:
        return self._unwrap(await self._get("/sub-users/"))

    async def geo_countries(self, name: str | None = None) -> list[dict[str, Any]]:
        return self._unwrap(await self._get("/geo/countries/", {"name": name} if name else None))

    async def geo_regions(self, country_code: str) -> list[dict[str, Any]]:
        return self._unwrap(await self._get("/geo/regions/", {"country_code": country_code}))

    async def geo_cities(self, country_code: str, region_code: str | None = None) -> list[dict[str, Any]]:
        params = {"country_code": country_code}
        if region_code:
            params["region_code"] = region_code
        return self._unwrap(await self._get("/geo/cities/", params))

    async def geo_zips(self, country_code: str, city_name: str | None = None) -> list[dict[str, Any]]:
        # city_name is optional: the v2 API returns all of a country's zips when
        # it's omitted (the ZIP-targeting UI path has no city to scope by).
        params: dict[str, Any] = {"country_code": country_code}
        if city_name:
            params["city_name"] = city_name
        return self._unwrap(await self._get("/geo/zips/", params))

    async def geo_isps(self, country_code: str, city_name: str | None = None) -> list[dict[str, Any]]:
        params = {"country_code": country_code}
        if city_name:
            params["city_name"] = city_name
        return self._unwrap(await self._get("/geo/isps/", params))
