"""Raw /scrape callers must not be able to set materializer-internal fields.

`parser_plan` (and `preset_meta`) on ScrapeRequest are populated by the
preset materializer only. If a raw /scrape/page caller could set them they
would trigger server-funded LLM calls (self-heal / AI extract) on the
server's keys, bypassing presets entirely. These tests pin the 422 guard.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from src.app import create_app


@pytest.fixture
def client(mocker):
    kiq_mock = AsyncMock()
    mocker.patch("src.scrape_service.scrape_page_task.kiq", new=kiq_mock)
    app = create_app()
    with TestClient(app) as c:
        c._kiq_mock = kiq_mock  # type: ignore[attr-defined]
        yield c


class TestRawScrapeRejectsInternalFields:
    def test_parser_plan_rejected_on_page(self, client):
        resp = client.post(
            "/api/v1/scrape/page",
            json={
                "url": "https://example.com",
                "parser_plan": {"llm_model": "openai/gpt-5.4-mini",
                                "output_schema": {"x": "string"}},
            },
        )
        assert resp.status_code == 422
        client._kiq_mock.assert_not_awaited()

    def test_preset_meta_rejected_on_page(self, client):
        resp = client.post(
            "/api/v1/scrape/page",
            json={
                "url": "https://example.com",
                "preset_meta": {"name": "x", "source": "y"},
            },
        )
        assert resp.status_code == 422

    def test_parser_plan_rejected_in_batch(self, client):
        resp = client.post(
            "/api/v1/scrape/pages",
            json={
                "pages": [
                    {"url": "https://example.com"},
                    {"url": "https://example.org",
                     "parser_plan": {"llm_model": "m"}},
                ]
            },
        )
        assert resp.status_code == 422
        client._kiq_mock.assert_not_awaited()

    def test_clean_request_still_works(self, client):
        resp = client.post(
            "/api/v1/scrape/page",
            json={"url": "https://example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["job_id"], str)
        assert len(data["job_id"]) > 0
