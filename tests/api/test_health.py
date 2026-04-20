from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.app import create_app


@pytest.fixture
def client():
    """TestClient for API tests"""
    app = create_app()
    with TestClient(app) as client:
        yield client


class TestHealthEndpoint:
    def test_health_check_returns_200(self, client):
        """Health check return 200"""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_health_check_response_format(self, client):
        """Health check return correct format"""
        response = client.get("/api/v1/health")
        data = response.json()

        assert "status" in data
        assert data["status"] == "ok"

    def test_health_check_no_side_effects(self, client):
        """Health check is idempotent"""
        response1 = client.get("/api/v1/health")
        response2 = client.get("/api/v1/health")

        assert response1.json() == response2.json()

    def test_health_check_multiple_calls(self, client):
        """Many calls health check"""
        for _ in range(5):
            response = client.get("/api/v1/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok", "workers": 2}
