from __future__ import annotations

from src.proxy.base import ProxyFailure, ProxyLease, ResolvedProxy
from src.proxy.models import ProxyConfig


class TestProxyFailure:
    def test_proxy_failure_dataclass(self):
        """ProxyFailure dataclass"""
        failure = ProxyFailure(status_code=403, error="Access denied")

        assert failure.status_code == 403
        assert failure.error == "Access denied"

    def test_proxy_failure_no_status(self):
        """ProxyFailure without status code"""
        failure = ProxyFailure(status_code=None, error="Network error")

        assert failure.status_code is None
        assert failure.error == "Network error"

    def test_proxy_failure_no_error(self):
        """ProxyFailure without message"""
        failure = ProxyFailure(status_code=500, error=None)

        assert failure.status_code == 500
        assert failure.error is None


class TestProxyLease:
    def test_proxy_lease_minimal(self):
        """Minimal ProxyLease"""
        config = ProxyConfig(server="http://proxy.com:8080")
        lease = ProxyLease(config=config)

        assert lease.config == config
        assert lease.source_id is None
        assert lease.change_ip_links is None

    def test_proxy_lease_with_source_id(self):
        """ProxyLease with source_id"""
        config = ProxyConfig(server="http://proxy.com:8080")
        lease = ProxyLease(config=config, source_id="proxy_123")

        assert lease.source_id == "proxy_123"

    def test_proxy_lease_with_change_ip(self):
        """ProxyLease with change_ip_links"""
        config = ProxyConfig(server="http://proxy.com:8080")
        lease = ProxyLease(
            config=config,
            source_id="mobile_1",
            change_ip_links=["https://api.example.com/change-ip/1"],
        )

        assert lease.change_ip_links == ["https://api.example.com/change-ip/1"]


class TestResolvedProxy:
    def test_resolved_proxy_with_lease(self):
        """ResolvedProxy with lease"""
        config = ProxyConfig(server="http://proxy.com:8080")
        lease = ProxyLease(config=config)
        resolved = ResolvedProxy(lease=lease)

        assert resolved.lease == lease
        assert resolved.provider is None

    def test_resolved_proxy_none(self):
        """ResolvedProxy without lease"""
        resolved = ResolvedProxy(lease=None)

        assert resolved.lease is None
        assert resolved.provider is None
