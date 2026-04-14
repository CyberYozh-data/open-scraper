from __future__ import annotations

import pytest
from unittest.mock import Mock, AsyncMock

from src.proxy.cyberyozh.session import CyberYozhSession
from src.proxy.cyberyozh.provider import CyberYozhProxyProvider
from src.proxy.base import ProxyFailure, ProxyLease
from src.proxy.models import ProxyConfig


class TestCyberYozhSession:
    @pytest.mark.asyncio
    async def test_session_init(self, mocker):
        """Session initialization"""
        provider = Mock(spec=CyberYozhProxyProvider)

        mock_lease = ProxyLease(
            config=ProxyConfig(server="http://proxy.com:8080"),
            source_id="1",
        )
        provider.acquire = AsyncMock(return_value=mock_lease)

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw="mobile",
            proxy_pool_id=None,
        )

        initialized_session = await session.init()

        assert initialized_session.exclude_ids == set()
        assert initialized_session.lease == mock_lease
        provider.acquire.assert_called_once_with(
            proxy_type_raw="mobile",
            proxy_pool_id=None,
        )

    @pytest.mark.asyncio
    async def test_session_exclude_ids_initialized(self, mocker):
        """exclude_ids initialization like empty set"""
        provider = Mock(spec=CyberYozhProxyProvider)
        provider.acquire = AsyncMock(return_value=Mock())

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw="res_static",
            proxy_pool_id=None,
        )

        await session.init()

        assert isinstance(session.exclude_ids, set)
        assert len(session.exclude_ids) == 0

    def test_session_max_attempts(self):
        """max_attempts delegates to provider"""
        provider = Mock(spec=CyberYozhProxyProvider)
        provider.max_attempts = Mock(return_value=5)

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw="mobile",
            proxy_pool_id=None,
        )

        result = session.max_attempts()

        assert result == 5
        provider.max_attempts.assert_called_once_with("mobile")

    def test_session_current_proxy(self):
        """current_proxy return lease.config"""
        provider = Mock(spec=CyberYozhProxyProvider)

        config = ProxyConfig(server="http://proxy.com:8080")
        lease = ProxyLease(config=config)

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw="res_static",
            proxy_pool_id=None,
            lease=lease,
        )

        result = session.current_proxy()

        assert result == config

    def test_session_current_proxy_none(self):
        """current_proxy return None if doesn't have lease"""
        provider = Mock(spec=CyberYozhProxyProvider)

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw="res_static",
            proxy_pool_id=None,
            lease=None,
        )

        result = session.current_proxy()

        assert result is None

    @pytest.mark.asyncio
    async def test_session_on_failure_success(self, mocker):
        """on_failure is being successfully restored"""
        provider = Mock(spec=CyberYozhProxyProvider)

        old_lease = Mock()
        new_lease = Mock()

        provider.recover = AsyncMock(return_value=(new_lease, True))

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw="mobile",
            proxy_pool_id=None,
            lease=old_lease,
            exclude_ids=set(),
        )

        failure = ProxyFailure(status_code=403, error="Forbidden")
        should_retry = await session.on_failure(failure)

        assert should_retry is True
        assert session.lease == new_lease
        provider.recover.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_on_failure_no_retry(self, mocker):
        """on_failure return False from provider"""
        provider = Mock(spec=CyberYozhProxyProvider)

        lease = Mock()
        provider.recover = AsyncMock(return_value=(lease, False))

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw="res_static",
            proxy_pool_id=None,
            lease=lease,
            exclude_ids=set(),
        )

        failure = ProxyFailure(status_code=500, error="Error")
        should_retry = await session.on_failure(failure)

        assert should_retry is False

    @pytest.mark.asyncio
    async def test_session_on_failure_updates_lease(self, mocker):
        """on_failure update lease"""
        provider = Mock(spec=CyberYozhProxyProvider)

        old_lease = ProxyLease(
            config=ProxyConfig(server="http://old.com:8080"),
            source_id="old",
        )
        new_lease = ProxyLease(
            config=ProxyConfig(server="http://new.com:8080"),
            source_id="new",
        )

        provider.recover = AsyncMock(return_value=(new_lease, True))

        session = CyberYozhSession(
            provider=provider,
            proxy_type_raw="res_rotating",
            proxy_pool_id=None,
            lease=old_lease,
            exclude_ids=set(),
        )

        failure = ProxyFailure(status_code=403, error="Blocked")
        await session.on_failure(failure)

        assert session.lease == new_lease
        assert session.lease.source_id == "new"
