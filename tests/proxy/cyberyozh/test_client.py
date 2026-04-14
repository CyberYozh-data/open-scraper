from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, Mock
import httpx

from src.proxy.cyberyozh.client import CyberYozhClient, OrderedProxy


class TestCyberYozhClient:
    @pytest.fixture
    def client(self):
        """Base client"""
        return CyberYozhClient(
            base_url="https://app.cyberyozh.com/api/v1",
            api_key="test_key_123",
        )

    def test_client_init(self, client):
        """Initializing client"""
        assert client._base_url == "https://app.cyberyozh.com/api/v1"
        assert client._api_key == "test_key_123"
        assert client._timeout_s == 15.0

    def test_client_init_strips_trailing_slash(self):
        """Initializing remove trailing slash"""
        client = CyberYozhClient(
            base_url="https://app.cyberyozh.com/api/v1/",
            api_key="key",
        )
        assert client._base_url == "https://app.cyberyozh.com/api/v1"

    def test_headers_generation(self, client):
        """Generation titles"""
        headers = client._headers()

        assert headers["accept"] == "application/json"
        assert headers["X-Api-Key"] == "test_key_123"


class TestProxyHistory:
    @pytest.fixture
    def client(self):
        return CyberYozhClient(
            base_url="https://app.cyberyozh.com/api/v1",
            api_key="test_key",
        )

    @pytest.mark.asyncio
    async def test_proxy_history_success_list_response(self, client, mocker):
        """Success history request (response - list)"""
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                "id": "1",
                "url": "http://proxy1.com:8080",
                "connection_login": "user1",
                "connection_password": "pass1",
                "system_status": "active",
                "expired": False,
                "change_ip_links": ["https://api.com/change-ip/1"],
                "connection_host": "proxy1.com",
                "connection_port": 8080,
            },
            {
                "id": "2",
                "url": "http://proxy2.com:8080",
                "connection_login": "user2",
                "connection_password": "pass2",
                "system_status": "active",
                "expired": False,
                "change_ip_links": [],
                "connection_host": "proxy2.com",
                "connection_port": 8080,
            },
        ]
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        result = await client.proxy_history(category="residential", expired=False)

        assert len(result) == 2
        assert isinstance(result[0], OrderedProxy)
        assert result[0].id == "1"
        assert result[0].url == "http://proxy1.com:8080"
        assert result[0].login == "user1"
        assert result[0].status == "active"
        assert result[0].expired is False

    @pytest.mark.asyncio
    async def test_proxy_history_success_dict_response(self, client, mocker):
        """Success history request (response - dict with results)"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "results": [
                {
                    "id": "1",
                    "url": "http://proxy1.com:8080",
                    "connection_login": "user1",
                    "connection_password": "pass1",
                    "system_status": "active",
                    "expired": False,
                    "change_ip_links": [],
                    "connection_host": "proxy1.com",
                    "connection_port": 8080,
                }
            ],
            "count": 1,
        }
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        result = await client.proxy_history(category="residential", expired=False)

        assert len(result) == 1
        assert result[0].id == "1"

    @pytest.mark.asyncio
    async def test_proxy_history_empty(self, client, mocker):
        """Empty response from API"""
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        result = await client.proxy_history(category="residential", expired=False)

        assert result == []

    @pytest.mark.asyncio
    async def test_proxy_history_http_error(self, client, mocker):
        """HTTP error in request"""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError(
                "Unauthorized",
                request=Mock(),
                response=mock_response,
            )
        )

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        with pytest.raises(httpx.HTTPStatusError):
            await client.proxy_history(category="residential", expired=False)

    @pytest.mark.asyncio
    async def test_proxy_history_invalid_response_format(self, client, mocker):
        """Response is not valid format"""
        mock_response = Mock()
        mock_response.json.return_value = {"error": "something"}
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        with pytest.raises(RuntimeError, match="Unexpected /proxies/history response shape"):
            await client.proxy_history(category="residential", expired=False)

    @pytest.mark.asyncio
    async def test_proxy_history_params(self, client, mocker):
        """Request params check"""
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        await client.proxy_history(category="lte", expired=True)

        # Check, get was called with correct params
        call_args = mock_client.get.call_args
        assert call_args[0][0] == "https://app.cyberyozh.com/api/v1/proxies/history/"
        assert call_args[1]["params"]["category"] == "lte"
        assert call_args[1]["params"]["expired"] == "true"


class TestRotatingCredentials:
    @pytest.fixture
    def client(self):
        return CyberYozhClient(
            base_url="https://app.cyberyozh.com/api/v1",
            api_key="test_key",
        )

    @pytest.mark.asyncio
    async def test_rotating_credentials_success(self, client, mocker):
        """Success get credentials"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "credentials": ["user1:pass1@proxy1.com:8080", "user2:pass2@proxy2.com:8080"]
        }
        mock_response.raise_for_status = Mock()
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        payload = {
            "connection_login": "user",
            "connection_password": "pass",
            "connection_host": "proxy.com",
            "connection_port": "8080",
            "session_type": "random",
        }

        result = await client.rotating_credentials(payload)

        assert len(result) == 2
        assert result[0] == "user1:pass1@proxy1.com:8080"
        assert result[1] == "user2:pass2@proxy2.com:8080"

    @pytest.mark.asyncio
    async def test_rotating_credentials_single_string(self, client, mocker):
        """Credentials like single row"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "credentials": "user:pass@proxy.com:8080"
        }
        mock_response.raise_for_status = Mock()
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        result = await client.rotating_credentials({})

        assert len(result) == 1
        assert result[0] == "user:pass@proxy.com:8080"

    @pytest.mark.asyncio
    async def test_rotating_credentials_with_geo(self, client, mocker):
        """Credentials with geo params"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "credentials": ["user:pass@proxy.com:8080"]
        }
        mock_response.raise_for_status = Mock()
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        payload = {
            "connection_host": "proxy.com",
            "connection_port": "8080",
            "country_code": "US",
            "region": "California",
            "city": "Los Angeles",
        }

        result = await client.rotating_credentials(payload)

        # Check, post were called with geo params
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["country_code"] == "US"
        assert call_args[1]["json"]["region"] == "California"
        assert call_args[1]["json"]["city"] == "Los Angeles"

    @pytest.mark.asyncio
    async def test_rotating_credentials_http_error(self, client, mocker):
        """HTTP error in get credentials"""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad request: invalid parameters"

        mock_request = Mock()

        mock_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError(
                "Bad request",
                request=mock_request,
                response=mock_response,
            )
        )

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        with pytest.raises(httpx.HTTPStatusError):
            await client.rotating_credentials({})

    @pytest.mark.asyncio
    async def test_rotating_credentials_empty(self, client, mocker):
        """Empty credentials response"""
        mock_response = Mock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = Mock()
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        result = await client.rotating_credentials({})

        assert result == []


class TestCallChangeIpLink:
    @pytest.fixture
    def client(self):
        return CyberYozhClient(
            base_url="https://app.cyberyozh.com/api/v1",
            api_key="test_key",
        )

    @pytest.mark.asyncio
    async def test_call_change_ip_link_success(self, client, mocker):
        """Success call change_ip"""
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        url = "https://api.cyberyozh.com/change-ip/123"
        await client.call_change_ip_link(url)

        # Check, get weer called with correct URL
        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert call_args[0][0] == url

    @pytest.mark.asyncio
    async def test_call_change_ip_link_http_error(self, client, mocker):
        """HTTP error in change_ip"""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError(
                "Not found",
                request=Mock(),
                response=mock_response,
            )
        )

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        with pytest.raises(httpx.HTTPStatusError):
            await client.call_change_ip_link("https://api.com/change-ip/123")
