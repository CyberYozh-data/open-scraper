from __future__ import annotations

import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.proxy.resolver import ProxyResolver, DirectSession
from src.proxy.cyberyozh.session import CyberYozhSession


class TestDirectSession:
    def test_direct_session_max_attempts(self):
        """DirectSession: max_attempts = 1"""
        session = DirectSession()
        assert session.max_attempts() == 1

    def test_direct_session_current_proxy(self):
        """DirectSession: current_proxy = None"""
        session = DirectSession()
        assert session.current_proxy() is None

    @pytest.mark.asyncio
    async def test_direct_session_on_failure(self):
        """DirectSession: on_failure = False"""
        session = DirectSession()
        result = await session.on_failure(Mock())
        assert result is False


class TestProxyResolver:
    def test_resolver_no_api_key(self):
        """ProxyResolver without API key"""
        with patch("src.proxy.resolver.settings") as mock_settings:
            mock_settings.cyberyozh_api_key = None
            mock_settings.cyberyozh_base_url = "https://app.cyberyozh.com"

            resolver = ProxyResolver()

            assert resolver._client is None

    def test_resolver_with_api_key(self):
        """ProxyResolver with API key"""
        with patch("src.proxy.resolver.settings") as mock_settings:
            from pydantic import SecretStr
            mock_settings.cyberyozh_api_key = SecretStr("test_key")
            mock_settings.cyberyozh_base_url = "https://app.cyberyozh.com"

            resolver = ProxyResolver()

            assert resolver._client is not None

    @pytest.mark.asyncio
    async def test_open_session_none_type(self):
        """open_session with proxy_type="none" → DirectSession"""
        with patch("src.proxy.resolver.settings") as mock_settings:
            mock_settings.cyberyozh_api_key = None

            resolver = ProxyResolver()
            session = await resolver.open_session(
                proxy_type="none",
                proxy_pool_id=None,
            )

            assert isinstance(session, DirectSession)

    @pytest.mark.asyncio
    async def test_open_session_no_type(self):
        """open_session with proxy_type=None → DirectSession"""
        with patch("src.proxy.resolver.settings") as mock_settings:
            mock_settings.cyberyozh_api_key = None

            resolver = ProxyResolver()
            session = await resolver.open_session(
                proxy_type=None,
                proxy_pool_id=None,
            )

            assert isinstance(session, DirectSession)

    @pytest.mark.asyncio
    async def test_open_session_no_client(self):
        """open_session without client → DirectSession"""
        with patch("src.proxy.resolver.settings") as mock_settings:
            mock_settings.cyberyozh_api_key = None

            resolver = ProxyResolver()
            session = await resolver.open_session(
                proxy_type="mobile",
                proxy_pool_id=None,
            )

            assert isinstance(session, DirectSession)

    @pytest.mark.asyncio
    async def test_open_session_cyberyozh(self, mocker):
        """open_session with proxy type → CyberYozhSession"""
        with patch("src.proxy.resolver.settings") as mock_settings:
            from pydantic import SecretStr
            mock_settings.cyberyozh_api_key = SecretStr("test_key")
            mock_settings.cyberyozh_base_url = "https://app.cyberyozh.com"

            resolver = ProxyResolver()

            mock_session = Mock(spec=CyberYozhSession)
            mock_session.init = AsyncMock(return_value=mock_session)

            with patch("src.proxy.resolver.CyberYozhSession", return_value=mock_session):
                session = await resolver.open_session(
                    proxy_type="mobile",
                    proxy_pool_id="pool_1",
                )

                assert session == mock_session
                mock_session.init.assert_called_once()

    @pytest.mark.asyncio
    async def test_open_session_with_geo(self, mocker):
        """open_session with geo params"""
        with patch("src.proxy.resolver.settings") as mock_settings:
            from pydantic import SecretStr
            mock_settings.cyberyozh_api_key = SecretStr("test_key")
            mock_settings.cyberyozh_base_url = "https://app.cyberyozh.com"

            resolver = ProxyResolver()

            mock_session = Mock(spec=CyberYozhSession)
            mock_session.init = AsyncMock(return_value=mock_session)

            geo = {
                "country_code": "US",
                "region": "California",
                "city": "Los Angeles",
            }

            with patch("src.proxy.resolver.CyberYozhSession", return_value=mock_session):
                with patch("src.proxy.resolver.CyberYozhProxyProvider") as mock_provider_cls:
                    session = await resolver.open_session(
                        proxy_type="res_rotating",
                        proxy_pool_id=None,
                        proxy_geo=geo,
                    )

                    # Check, provider was created with geo
                    mock_provider_cls.assert_called_once()
                    call_kwargs = mock_provider_cls.call_args[1]
                    assert call_kwargs["geo"] == geo
