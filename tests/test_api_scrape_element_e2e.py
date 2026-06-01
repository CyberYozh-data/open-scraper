import base64
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.app import create_app


pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session")
def client():
    app = create_app()
    with TestClient(app) as client:
        yield client


def wait_done(client: TestClient, job_id: str, timeout_s: float = 30.0):
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        response = client.get(f"/api/v1/scrape/{job_id}")
        assert response.status_code == 200
        data = response.json()

        if data["status"] in ("done", "failed"):
            return data

        time.sleep(0.5)

    raise TimeoutError("job did not finish in time")


def test_element_selector_crops_to_element(client: TestClient, httpserver):
    """element_selector on a visible element returns a cropped PNG with element status."""
    html = (
        "<!doctype html><html><body>"
        '<div id="x" style="width:200px;height:100px;background:red;margin:50px"></div>'
        "</body></html>"
    )
    httpserver.expect_request("/page.html").respond_with_data(html, content_type="text/html")
    url = httpserver.url_for("/page.html")

    response = client.post(
        "/api/v1/scrape/page",
        json={
            "url": url,
            "proxy_type": "none",
            "render": True,
            "screenshot": True,
            "element_selector": "#x",
            "wait_until": "domcontentloaded",
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    status = wait_done(client, job_id)
    if status["status"] == "failed":
        results = client.get(f"/api/v1/scrape/{job_id}/results").json()
        pytest.fail(f"Job failed. Results: {results}")
    assert status["status"] == "done"

    results_response = client.get(f"/api/v1/scrape/{job_id}/results")
    assert results_response.status_code == 200
    results = results_response.json()["results"]
    assert len(results) == 1

    result = results[0]
    assert result["element_screenshot_status"] == "element"

    raw_png = base64.b64decode(result["screenshot_base64"])
    image = Image.open(io.BytesIO(raw_png))
    width, height = image.size
    # Element is 200x100; expect ~24px padding on each side (48px total), with tolerance
    assert 200 + 30 <= width <= 200 + 70, f"width {width} not in expected range [230, 270]"
    assert 100 + 30 <= height <= 100 + 70, f"height {height} not in expected range [130, 170]"


def test_element_selector_ignored_when_screenshot_false(client: TestClient, httpserver):
    """element_selector has no effect when screenshot=False; status is no_screenshot."""
    html = (
        "<!doctype html><html><body>"
        '<div id="x" style="width:200px;height:100px;background:red;margin:50px"></div>'
        "</body></html>"
    )
    httpserver.expect_request("/page2.html").respond_with_data(html, content_type="text/html")
    url = httpserver.url_for("/page2.html")

    response = client.post(
        "/api/v1/scrape/page",
        json={
            "url": url,
            "proxy_type": "none",
            "render": True,
            "screenshot": False,
            "element_selector": "#x",
            "wait_until": "domcontentloaded",
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    status = wait_done(client, job_id)
    if status["status"] == "failed":
        results = client.get(f"/api/v1/scrape/{job_id}/results").json()
        pytest.fail(f"Job failed. Results: {results}")
    assert status["status"] == "done"

    results_response = client.get(f"/api/v1/scrape/{job_id}/results")
    assert results_response.status_code == 200
    results = results_response.json()["results"]
    assert len(results) == 1

    result = results[0]
    assert result["element_screenshot_status"] == "no_screenshot"
    assert result["screenshot_base64"] is None
