import time

import pytest
from fastapi.testclient import TestClient

from src.app import create_app


pytestmark = pytest.mark.e2e  # All test in this file like e2e


@pytest.fixture(scope="session")
def client():
    app = create_app()
    with TestClient(app) as client:
        yield client


def wait_done(client: TestClient, job_id: str, timeout_s: float = 20.0):
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        response = client.get(f"/api/v1/scrape/{job_id}")
        assert response.status_code == 200
        data = response.json()

        if data["status"] in ("done", "failed"):
            return data

        time.sleep(0.5)

    raise TimeoutError("job did not finish in time")


def test_single_page_smoke(client: TestClient):
    response = client.post(
        "/api/v1/scrape/page",
        json={
            "url": "https://example.com",
            "proxy_type": "none",
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    status = wait_done(client, job_id)
    assert status["status"] == "done"
    assert status["done"] == 1
    assert status["total"] == 1

    response = client.get(f"/api/v1/scrape/{job_id}/results")
    assert response.status_code == 200
    results = response.json()["results"]

    assert len(results) == 1
    assert results[0]["meta"]["status_code"] == 200


def test_batch_smoke(client: TestClient):
    response = client.post(
        "/api/v1/scrape/pages",
        json={
            "pages": [
                {"url": "https://example.com", "proxy_type": "none"},
                {"url": "https://example.org", "proxy_type": "none"},
            ]
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    status = wait_done(client, job_id)
    assert status["status"] == "done"
    assert status["done"] == 2
    assert status["total"] == 2

    response = client.get(f"/api/v1/scrape/{job_id}/results")
    assert response.status_code == 200
    results = response.json()["results"]

    assert len(results) == 2
    for res in results:
        assert res["meta"]["status_code"] == 200


def test_extractor_smoke(client: TestClient):
    response = client.post(
        "/api/v1/scrape/page",
        json={
            "url": "https://example.com",
            "proxy_type": "none",
            "extract": {
                "type": "css",
                "fields": {
                    "title": {
                        "selector": "h1",
                        "attr": "text",
                        "all": False,
                    }
                },
            },
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    status = wait_done(client, job_id)
    assert status["status"] == "done"

    response = client.get(f"/api/v1/scrape/{job_id}/results")
    results = response.json()["results"]

    assert results[0]["data"]["title"] is not None
