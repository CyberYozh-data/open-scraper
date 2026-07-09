from __future__ import annotations

import re
import pytest
from unittest.mock import AsyncMock

from src.proxy.cyberyozh.username import (
    UsernameParts,
    assemble_username,
    gen_sticky_id,
    resolve_username_parts,
    targeting_suffix,
)


def test_assemble_order_and_omission():
    parts = UsernameParts(
        real_login="Giterfull897e009c",
        country_suffix="c-ru",
        filter_suffix="filter-iqs",
    )
    assert assemble_username(parts) == "Giterfull897e009c-c-ru-filter-iqs"


def test_assemble_full_order():
    parts = UsernameParts(
        real_login="u1",
        country_suffix="c-us",
        region_suffix="r-5",
        city_suffix="ct-nyc",
        zip_suffix=None,
        isp_suffix="isp-x",
        session_suffix="s-Ab3xK9pQ",
        ttl_suffix="ttl-5m",
        filter_suffix="filter-iqs",
    )
    assert assemble_username(parts) == "u1-c-us-r-5-ct-nyc-isp-x-s-Ab3xK9pQ-ttl-5m-filter-iqs"


def test_targeting_suffix_excludes_login():
    parts = UsernameParts(
        real_login="u1",
        country_suffix="c-us",
        session_suffix="s-Ab3xK9pQ",
        filter_suffix="filter-iqs",
    )
    assert targeting_suffix(parts) == "c-us-s-Ab3xK9pQ-filter-iqs"


def test_targeting_suffix_robust_to_dashed_login():
    # The login is never part of the suffix, even when it contains dashes — this
    # is why we derive targeting from structured parts instead of splitting the
    # assembled username string.
    parts = UsernameParts(real_login="acct-with-dash", country_suffix="c-ru")
    assert targeting_suffix(parts) == "c-ru"
    assert assemble_username(parts) == "acct-with-dash-c-ru"


def test_targeting_suffix_none_without_targeting():
    assert targeting_suffix(UsernameParts(real_login="bare")) is None


def test_gen_sticky_id_shape():
    sid = gen_sticky_id()
    assert len(sid) == 8 and re.fullmatch(r"[A-Za-z0-9]{8}", sid)


@pytest.mark.asyncio
async def test_resolve_country_and_filter_only():
    client = AsyncMock()
    parts = await resolve_username_parts(
        client,
        real_login="RL",
        proxy_geo={"country_code": "RU"},
        prem_opts={"ip_filter": "quality-security", "session_type": "rotating"},
    )
    assert parts.country_suffix == "c-ru"
    assert parts.filter_suffix == "filter-iqs"
    assert parts.session_suffix is None
    client.geo_regions.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_region_suffix_from_geo():
    client = AsyncMock()
    client.geo_regions.return_value = [
        {"code": 5, "name": "Moscow Oblast", "suffix": "r-5"},
        {"code": 2, "name": "Leningrad", "suffix": "r-2"},
    ]
    parts = await resolve_username_parts(
        client,
        real_login="RL",
        proxy_geo={"country_code": "RU", "region": "Moscow Oblast"},
        prem_opts={"ip_filter": "max-size-security"},
    )
    assert parts.region_suffix == "r-5"
    assert parts.filter_suffix is None  # max-size = omitted


@pytest.mark.asyncio
async def test_resolve_sticky_autogenerates_id_and_ttl():
    client = AsyncMock()
    parts = await resolve_username_parts(
        client,
        real_login="RL",
        proxy_geo={"country_code": "RU"},
        prem_opts={"session_type": "sticky", "rotation_minutes": 10},
    )
    assert parts.session_suffix and parts.session_suffix.startswith("s-")
    assert parts.ttl_suffix == "ttl-10m"
