from __future__ import annotations

from src.proxy.models import ProxyConfig


class TestProxyConfig:
    def test_proxy_config_minimal(self):
        """Minimal proxy config"""
        config = ProxyConfig(server="http://proxy.example.com:8080")

        assert config.server == "http://proxy.example.com:8080"
        assert config.username is None
        assert config.password is None

    def test_proxy_config_with_auth(self):
        """Proxy woth auth"""
        config = ProxyConfig(
            server="http://proxy.example.com:8080",
            username="user",
            password="pass",
        )

        assert config.server == "http://proxy.example.com:8080"
        assert config.username == "user"
        assert config.password == "pass"

    def test_proxy_config_http(self):
        """HTTP proxy"""
        config = ProxyConfig(server="http://10.0.0.1:3128")
        assert config.server == "http://10.0.0.1:3128"

    def test_proxy_config_https(self):
        """HTTPS proxy"""
        config = ProxyConfig(server="https://secure-proxy.com:443")
        assert config.server == "https://secure-proxy.com:443"

    def test_proxy_config_socks5(self):
        """SOCKS5 proxy"""
        config = ProxyConfig(
            server="socks5://socks-proxy.com:1080",
            username="user",
            password="secret",
        )

        assert config.server == "socks5://socks-proxy.com:1080"
        assert config.username == "user"
        assert config.password == "secret"

    def test_proxy_config_validation(self):
        """Fields validation"""
        config = ProxyConfig(server="http://proxy:8080")

        # Check, model correct created
        assert isinstance(config, ProxyConfig)
        assert config.server is not None
