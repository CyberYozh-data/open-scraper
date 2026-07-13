from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from typing import Any

from src.proxy.base import ProxyConfigError

_STICKY_ALPHABET = string.ascii_letters + string.digits

# ip_filter enum -> username suffix token (no leading dash). max-size = omitted.
IP_FILTER_SUFFIX: dict[str, str | None] = {
    "max-size-security": None,
    "max-speed-security": "filter-speed",
    "quality-security": "filter-iqs",
    "speed-quality-security": "filter-speed+iqs",
}


@dataclass
class UsernameParts:
    real_login: str
    country_suffix: str | None = None
    region_suffix: str | None = None
    city_suffix: str | None = None
    zip_suffix: str | None = None
    isp_suffix: str | None = None
    session_suffix: str | None = None
    ttl_suffix: str | None = None
    filter_suffix: str | None = None


def _suffix_tokens(parts: UsernameParts) -> list[str]:
    """The targeting tokens in the provider's required order (no base login):
    country, region, city, zip, isp, session, ttl, filter."""
    return [
        suffix
        for suffix in (
            parts.country_suffix,
            parts.region_suffix,
            parts.city_suffix,
            parts.zip_suffix,
            parts.isp_suffix,
            parts.session_suffix,
            parts.ttl_suffix,
            parts.filter_suffix,
        )
        if suffix
    ]


def assemble_username(parts: UsernameParts) -> str:
    """Join real_login + suffixes with '-' in the provider's required order:
    base_login, country, region, city, zip, isp, session, ttl, filter."""
    return "-".join([parts.real_login, *_suffix_tokens(parts)])


def targeting_suffix(parts: UsernameParts) -> str | None:
    """The resolved targeting tokens only (no base login) — safe to echo to
    clients, since it never carries a credential. None when no targeting was
    applied (a plain rotating exit)."""
    tokens = _suffix_tokens(parts)
    return "-".join(tokens) if tokens else None


def gen_sticky_id(n: int = 8) -> str:
    return "".join(secrets.choice(_STICKY_ALPHABET) for _ in range(n))


def _find_suffix(
    items: list[dict[str, Any]], *, key: str, value: str, field: str, country: str
) -> str:
    """`field`/`country` are for the error messages only — they name the
    request field being resolved (region/city/isp/zip) and the country the
    catalog lookup ran under, since the message reaches clients verbatim
    (422 detail on /resolve, `error` on results)."""
    target = str(value).strip().lower()
    matched_without_suffix = False
    for item in items:
        if str(item.get(key, "")).strip().lower() == target:
            suffix = item.get("suffix")
            if suffix:
                return str(suffix)
            matched_without_suffix = True
    if matched_without_suffix:
        # The catalog knows the name but the entry carries no suffix —
        # upstream data breakage, not user input (RuntimeError → 502).
        raise RuntimeError(
            f"v2 catalog entry for {field} {value!r} (country {country!r}) has no suffix"
        )
    raise ProxyConfigError(f"unknown {field} {value!r} for country {country!r} in the v2 geo catalog")


async def resolve_geo_parts(
    client: Any,
    *,
    real_login: str,
    proxy_geo: dict[str, Any] | None,
    prem_opts: dict[str, Any] | None,
) -> UsernameParts:
    """Resolve the STATIC username parts (country/region/city/zip/isp/filter) from
    request geo + prem options, reading suffixes from the v2 /geo endpoints (never
    hardcoded). These don't change between retries of the same session, so the
    provider resolves them once and caches the result — this is the part that
    issues the geo HTTP lookups. The per-attempt sticky/ttl tokens are applied
    separately via apply_session_suffix()."""
    geo = proxy_geo or {}
    opts = prem_opts or {}
    country_code = (geo.get("country_code") or "").strip()
    parts = UsernameParts(real_login=real_login)

    if country_code:
        parts.country_suffix = f"c-{country_code.lower()}"

    region = geo.get("region")
    city = geo.get("city")
    zip_code = opts.get("zip")

    if zip_code and country_code and city:
        # zip lookup is scoped by city in the v2 API
        zips = await client.geo_zips(country_code, city)
        parts.zip_suffix = _find_suffix(
            zips, key="zip", value=zip_code, field="zip", country=country_code)
    elif zip_code and country_code:
        # best-effort: some accounts allow zip without a city scope
        zips = await client.geo_zips(country_code, "")
        parts.zip_suffix = _find_suffix(
            zips, key="zip", value=zip_code, field="zip", country=country_code)
    else:
        if region and country_code:
            regions = await client.geo_regions(country_code)
            parts.region_suffix = _find_suffix(
                regions, key="name", value=region, field="region", country=country_code)
        if city and country_code:
            cities = await client.geo_cities(country_code)
            parts.city_suffix = _find_suffix(
                cities, key="name", value=city, field="city", country=country_code)

    isp = opts.get("isp")
    if isp and country_code:
        isps = await client.geo_isps(country_code)
        parts.isp_suffix = _find_suffix(
            isps, key="name", value=isp, field="isp", country=country_code)

    parts.filter_suffix = IP_FILTER_SUFFIX.get(opts.get("ip_filter", "max-size-security"))

    return parts


def apply_session_suffix(parts: UsernameParts, prem_opts: dict[str, Any] | None) -> UsernameParts:
    """Set (or clear) the per-attempt sticky-session + ttl tokens from prem_opts.
    Pure and idempotent — no HTTP — so the provider can reapply it to a cached
    parts on every retry with a freshly-minted sticky_id. Mutates and returns
    `parts`."""
    opts = prem_opts or {}
    if opts.get("session_type") == "sticky":
        parts.session_suffix = f"s-{opts.get('sticky_id') or gen_sticky_id()}"
        rot = opts.get("rotation_minutes")
        parts.ttl_suffix = f"ttl-{int(rot)}m" if rot else None
    else:
        parts.session_suffix = None
        parts.ttl_suffix = None
    return parts


async def resolve_username_parts(
    client: Any,
    *,
    real_login: str,
    proxy_geo: dict[str, Any] | None,
    prem_opts: dict[str, Any] | None,
) -> UsernameParts:
    """Full resolution = static geo parts + per-attempt session suffix. Kept as a
    convenience wrapper over resolve_geo_parts() + apply_session_suffix()."""
    parts = await resolve_geo_parts(
        client, real_login=real_login, proxy_geo=proxy_geo, prem_opts=prem_opts
    )
    return apply_session_suffix(parts, prem_opts)
