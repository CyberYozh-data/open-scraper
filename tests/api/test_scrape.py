from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock, patch

from src.app import create_app
from src.jobs import JobRecord
from src.schemas import ScrapeResponse, ScrapeMeta


@pytest.fixture
def client():
    """TestClient for API tests"""
    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def mock_job_queue(mocker):
    """Mock JobQueue for tests"""
    queue = AsyncMock()
    queue.submit = AsyncMock(return_value="job_123")
    queue.get = AsyncMock(return_value=JobRecord(
        job_id="job_123",
        status="done",
        pages=[],
        total=1,
        done=1,
        results=[
            ScrapeResponse(
                request_id="req_1",
                took_ms=100,
                meta=ScrapeMeta(
                    url="https://example.com",
                    device="desktop",
                    proxy_type="none",
                    retries=0,
                ),
            )
        ],
    ))

    mocker.patch("src.api.scrape.get_job_queue", return_value=queue)
    return queue


class TestScrapePageEndpoint:
    def test_scrape_page_minimal(self, client, mock_job_queue):
        """Minimal request /scrape/page"""
        response = client.post(
            "/api/v1/scrape/page",
            json={"url": "https://example.com"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data

    def test_scrape_page_returns_job_id(self, client, mock_job_queue):
        """POST /scrape/page return job_id"""
        response = client.post(
            "/api/v1/scrape/page",
            json={"url": "https://example.com"},
        )

        data = response.json()
        assert data["job_id"] == "job_123"

    def test_scrape_page_with_proxy(self, client, mock_job_queue):
        """POST /scrape/page with proxy_type"""
        response = client.post(
            "/api/v1/scrape/page",
            json={
                "url": "https://example.com",
                "proxy_type": "mobile",
            },
        )

        assert response.status_code == 200

    def test_scrape_page_with_extract(self, client, mock_job_queue):
        """POST /scrape/page with extract rules"""
        response = client.post(
            "/api/v1/scrape/page",
            json={
                "url": "https://example.com",
                "extract": {
                    "type": "css",
                    "fields": {
                        "title": {
                            "selector": "h1",
                        }
                    },
                },
            },
        )

        assert response.status_code == 200

    def test_scrape_page_with_all_options(self, client, mock_job_queue):
        """POST /scrape/page with all params"""
        response = client.post(
            "/api/v1/scrape/page",
            json={
                "url": "https://example.com",
                "render": True,
                "wait_until": "networkidle",
                "device": "mobile",
                "proxy_type": "res_rotating",
                "raw_html": True,
                "screenshot": True,
            },
        )

        assert response.status_code == 200

    def test_scrape_page_with_proxy_geo(self, client, mock_job_queue):
        """POST /scrape/page with proxy_geo"""
        response = client.post(
            "/api/v1/scrape/page",
            json={
                "url": "https://example.com",
                "proxy_type": "res_rotating",
                "proxy_geo": {"country_code": "GB", "city": "London"},
            },
        )

        assert response.status_code == 200

    def test_scrape_page_validation_error(self, client, mock_job_queue):
        """POST /scrape/page with not valid URL"""
        response = client.post(
            "/api/v1/scrape/page",
            json={"url": "not-a-url"},
        )

        assert response.status_code == 422


class TestScrapePagesEndpoint:
    def test_scrape_pages_single(self, client, mock_job_queue):
        """POST /scrape/pageswith single page"""
        response = client.post(
            "/api/v1/scrape/pages",
            json={
                "pages": [
                    {"url": "https://example.com"},
                ]
            },
        )

        assert response.status_code == 200

    def test_scrape_pages_multiple(self, client, mock_job_queue):
        """POST /scrape/pages with a few pages"""
        response = client.post(
            "/api/v1/scrape/pages",
            json={
                "pages": [
                    {"url": "https://example.com"},
                    {"url": "https://example.org"},
                    {"url": "https://example.net"},
                ]
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data

    def test_scrape_pages_returns_job_id(self, client, mock_job_queue):
        """POST /scrape/pages return job_id"""
        response = client.post(
            "/api/v1/scrape/pages",
            json={
                "pages": [
                    {"url": "https://example.com"},
                ]
            },
        )

        data = response.json()
        assert data["job_id"] == "job_123"

    def test_scrape_pages_empty_list(self, client, mock_job_queue):
        """POST /scrape/pages with empty list"""
        response = client.post(
            "/api/v1/scrape/pages",
            json={"pages": []},
        )

        # Empty list is valid
        assert response.status_code == 200


class TestScrapeStatusEndpoint:
    def test_scrape_status_queued(self, client, mocker):
        """GET /scrape/{job_id} for queued job"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="queued",
            pages=[],
            total=1,
            done=0,
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["done"] == 0
        assert data["total"] == 1

    def test_scrape_status_running(self, client, mocker):
        """GET /scrape/{job_id} for running job"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="running",
            pages=[],
            total=5,
            done=2,
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert data["done"] == 2
        assert data["total"] == 5

    def test_scrape_status_done(self, client, mocker):
        """GET /scrape/{job_id} for done job"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="done",
            pages=[],
            total=1,
            done=1,
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "done"
        assert data["done"] == 1
        assert data["total"] == 1

    def test_scrape_status_failed(self, client, mocker):
        """GET /scrape/{job_id} for failed job"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="failed",
            pages=[],
            total=1,
            done=0,
            error="Worker error",
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"] == "Worker error"

    def test_scrape_status_not_found(self, client, mocker):
        """GET /scrape/{job_id} for not exists task"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=None)
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/non_existent")

        assert response.status_code == 404
        data = response.json()
        assert "job_not_found" in data["detail"]

    def test_scrape_status_response_format(self, client, mocker):
        """GET /scrape/{job_id} return valid format"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="running",
            pages=[],
            total=10,
            done=5,
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123")

        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert "done" in data
        assert "total" in data
        assert "error" in data


class TestScrapeResultsEndpoint:
    def test_scrape_results_success(self, client, mocker):
        """GET /scrape/{job_id}/results for finished job"""
        results = [
            ScrapeResponse(
                request_id="req_1",
                took_ms=100,
                meta=ScrapeMeta(
                    url="https://example.com",
                    device="desktop",
                    proxy_type="none",
                    retries=0,
                ),
            )
        ]

        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="done",
            pages=[],
            total=1,
            done=1,
            results=results,
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123/results")

        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "results" in data
        assert len(data["results"]) == 1

    def test_scrape_results_not_found(self, client, mocker):
        """GET /scrape/{job_id}/results for not exists job"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=None)
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/non_existent/results")

        assert response.status_code == 404

    def test_scrape_results_not_done_queued(self, client, mocker):
        """GET /scrape/{job_id}/results for queued job"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="queued",
            pages=[],
            total=1,
            done=0,
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123/results")

        assert response.status_code == 409
        data = response.json()
        assert "job_not_done" in data["detail"]

    def test_scrape_results_not_done_running(self, client, mocker):
        """GET /scrape/{job_id}/results for running job"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="running",
            pages=[],
            total=1,
            done=0,
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123/results")

        assert response.status_code == 409

    def test_scrape_results_failed_job_returns_200(self, client, mocker):
        """GET /scrape/{job_id}/results for failed job returns 200 with error info"""
        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="failed",
            pages=[],
            total=1,
            done=0,
            error="Worker crashed",
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123/results")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"] == "Worker crashed"
        assert data["results"] is None

    def test_scrape_results_response_format(self, client, mocker):
        """GET /scrape/{job_id}/results return correct format"""
        results = [
            ScrapeResponse(
                request_id="req_1",
                took_ms=100,
                meta=ScrapeMeta(
                    url="https://example.com",
                    device="desktop",
                    proxy_type="none",
                    retries=0,
                ),
                data={"title": "Test"},
            )
        ]

        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="done",
            pages=[],
            total=1,
            done=1,
            results=results,
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123/results")

        data = response.json()
        assert data["job_id"] == "job_123"
        assert isinstance(data["results"], list)
        assert data["results"][0]["data"]["title"] == "Test"

    def test_scrape_results_multiple_pages(self, client, mocker):
        """GET /scrape/{job_id}/results with a few results"""
        results = [
            ScrapeResponse(
                request_id="req_1",
                took_ms=100,
                meta=ScrapeMeta(
                    url="https://example.com",
                    device="desktop",
                    proxy_type="none",
                    retries=0,
                ),
            ),
            ScrapeResponse(
                request_id="req_2",
                took_ms=150,
                meta=ScrapeMeta(
                    url="https://example.org",
                    device="desktop",
                    proxy_type="none",
                    retries=0,
                ),
            ),
        ]

        queue = AsyncMock()
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="done",
            pages=[],
            total=2,
            done=2,
            results=results,
        ))
        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        response = client.get("/api/v1/scrape/job_123/results")

        data = response.json()
        assert len(data["results"]) == 2


class TestIntegrationFlows:
    def test_full_flow_page(self, client, mocker):
        """Full flow: page -> status -> results"""
        # Mock queue for different steps
        queue = AsyncMock()

        # Submit
        queue.submit = AsyncMock(return_value="job_123")

        # Status (running)
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="running",
            pages=[],
            total=1,
            done=0,
        ))

        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        # 1. Submit
        response = client.post(
            "/api/v1/scrape/page",
            json={"url": "https://example.com"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # 2. Status
        response = client.get(f"/api/v1/scrape/{job_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "running"

        # 3. Update mock for done
        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_123",
            status="done",
            pages=[],
            total=1,
            done=1,
            results=[
                ScrapeResponse(
                    request_id="req_1",
                    took_ms=100,
                    meta=ScrapeMeta(
                        url="https://example.com",
                        device="desktop",
                        proxy_type="none",
                        retries=0,
                    ),
                )
            ],
        ))

        # 4. Results
        response = client.get(f"/api/v1/scrape/{job_id}/results")
        assert response.status_code == 200
        assert len(response.json()["results"]) == 1

    def test_full_flow_pages(self, client, mocker):
        """Full flow: pages -> status -> results"""
        queue = AsyncMock()
        queue.submit = AsyncMock(return_value="job_456")

        queue.get = AsyncMock(return_value=JobRecord(
            job_id="job_456",
            status="done",
            pages=[],
            total=2,
            done=2,
            results=[
                ScrapeResponse(
                    request_id="req_1",
                    took_ms=100,
                    meta=ScrapeMeta(
                        url="https://example.com",
                        device="desktop",
                        proxy_type="none",
                        retries=0,
                    ),
                ),
                ScrapeResponse(
                    request_id="req_2",
                    took_ms=120,
                    meta=ScrapeMeta(
                        url="https://example.org",
                        device="desktop",
                        proxy_type="none",
                        retries=0,
                    ),
                ),
            ],
        ))

        mocker.patch("src.api.scrape.get_job_queue", return_value=queue)

        # Submit
        response = client.post(
            "/api/v1/scrape/pages",
            json={
                "pages": [
                    {"url": "https://example.com"},
                    {"url": "https://example.org"},
                ]
            },
        )
        job_id = response.json()["job_id"]

        # Results
        response = client.get(f"/api/v1/scrape/{job_id}/results")
        assert response.status_code == 200
        assert len(response.json()["results"]) == 2
