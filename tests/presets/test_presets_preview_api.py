from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.presets.service as service_mod
from src.api.presets import get_preset_store, router as presets_router
from src.extract.models import FieldRule
from src.presets.models import LocaleProfile, ParsingInstructions, Preset
from src.presets.store import BuiltInRegistry, FilePresetStore, PresetStore

SAMPLE_HTML = "<html><body><h1 id='t'>Widget</h1><span class='p'>$9.99</span></body></html>"


@pytest.fixture
def store(tmp_path: Path) -> PresetStore:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    bp = Preset(
        name="amazon_product",
        source="amazon",
        kind="builtin",
        request_defaults={"device": "desktop"},
        locales={"us": LocaleProfile(domain="com", country="US")},
        default_locale="us",
        parsing_instructions=ParsingInstructions(
            type="css",
            fields={"title": FieldRule(selector="#t", required=True)},
        ),
        updated_at=1.0,
    )
    (builtin / "amazon_product.json").write_text(
        json.dumps(bp.model_dump(mode="json"))
    )
    return PresetStore(
        builtin=BuiltInRegistry(base_path=builtin),
        user=FilePresetStore(base_path=user),
    )


@pytest.fixture
def client(store: PresetStore) -> TestClient:
    app = FastAPI()
    app.include_router(presets_router, prefix="/api/v1/presets")
    app.dependency_overrides[get_preset_store] = lambda: store
    return TestClient(app)


class TestPreviewManual:
    def test_manual_instructions_return_extracted(self, client: TestClient):
        resp = client.post(
            "/api/v1/presets/preview",
            json={
                "mode": "manual",
                "sample_html": SAMPLE_HTML,
                "parsing_instructions": {
                    "type": "css",
                    "fields": {"title": {"selector": "#t"}},
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["extracted"]["title"] == "Widget"
        assert body["mode"] == "deterministic"

    def test_manual_without_instructions_422(self, client: TestClient):
        resp = client.post(
            "/api/v1/presets/preview",
            json={"mode": "manual", "sample_html": SAMPLE_HTML},
        )
        assert resp.status_code == 422
        assert "parsing_instructions" in resp.json()["detail"]

    def test_preview_does_not_persist(self, client: TestClient):
        before = client.get("/api/v1/presets?kind=user").json()["items"]
        client.post(
            "/api/v1/presets/preview",
            json={
                "mode": "manual",
                "sample_html": SAMPLE_HTML,
                "parsing_instructions": {
                    "type": "css",
                    "fields": {"title": {"selector": "#t"}},
                },
            },
        )
        after = client.get("/api/v1/presets?kind=user").json()["items"]
        assert before == after == []


class TestPreviewFromSchema:
    def test_from_schema_generates_then_previews(self, client: TestClient, mocker):
        mocker.patch.object(
            service_mod,
            "generate_selectors",
            new=mocker.AsyncMock(
                return_value=ParsingInstructions(
                    type="css",
                    fields={"title": FieldRule(selector="#t", required=True)},
                )
            ),
        )
        resp = client.post(
            "/api/v1/presets/preview",
            json={
                "mode": "from_schema",
                "sample_html": SAMPLE_HTML,
                "schema": {"title": "string"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["parsing_instructions"]["fields"]["title"]["selector"] == "#t"
        assert body["extracted"]["title"] == "Widget"

    def test_from_schema_llm_failure_502(self, client: TestClient, mocker):
        from src.presets.llm.client import LLMError

        mocker.patch.object(
            service_mod,
            "generate_selectors",
            new=mocker.AsyncMock(side_effect=LLMError("model down")),
        )
        resp = client.post(
            "/api/v1/presets/preview",
            json={
                "mode": "from_schema",
                "sample_html": SAMPLE_HTML,
                "schema": {"title": "string"},
            },
        )
        assert resp.status_code == 502


class TestPreviewFromPrompt:
    def test_from_prompt_infers_then_previews(self, client: TestClient, mocker):
        mocker.patch.object(
            service_mod,
            "infer_schema",
            new=mocker.AsyncMock(return_value={"title": {"type": "string"}}),
        )
        mocker.patch.object(
            service_mod,
            "generate_selectors",
            new=mocker.AsyncMock(
                return_value=ParsingInstructions(
                    type="css", fields={"title": FieldRule(selector="#t")}
                )
            ),
        )
        resp = client.post(
            "/api/v1/presets/preview",
            json={
                "mode": "from_prompt",
                "sample_html": SAMPLE_HTML,
                "description": "grab the title",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["output_schema"] == {"title": {"type": "string"}}
        assert body["extracted"]["title"] == "Widget"

    def test_from_prompt_llm_failure_502(self, client: TestClient, mocker):
        from src.presets.llm.client import LLMError

        mocker.patch.object(
            service_mod,
            "infer_schema",
            new=mocker.AsyncMock(side_effect=LLMError("schema fail")),
        )
        resp = client.post(
            "/api/v1/presets/preview",
            json={
                "mode": "from_prompt",
                "sample_html": SAMPLE_HTML,
                "description": "title",
            },
        )
        assert resp.status_code == 502


class TestPreviewValidation:
    def test_ssrf_url_rejected(self, client: TestClient):
        resp = client.post(
            "/api/v1/presets/preview",
            json={"mode": "manual", "sample_url": "http://169.254.169.254/latest"},
        )
        assert resp.status_code == 400

    def test_no_sample_422(self, client: TestClient):
        resp = client.post(
            "/api/v1/presets/preview",
            json={
                "mode": "manual",
                "parsing_instructions": {"type": "css", "fields": {}},
            },
        )
        assert resp.status_code == 422

    def test_malformed_instructions_422(self, client: TestClient):
        # "not_a_valid_type" is rejected by ExtractType = Literal["css", "xpath"]
        resp = client.post(
            "/api/v1/presets/preview",
            json={
                "mode": "manual",
                "sample_html": SAMPLE_HTML,
                "parsing_instructions": {"type": "not_a_valid_type", "fields": {}},
            },
        )
        assert resp.status_code == 422

    def test_from_prompt_without_description_422(self, client: TestClient):
        resp = client.post(
            "/api/v1/presets/preview",
            json={"mode": "from_prompt", "sample_html": SAMPLE_HTML},
        )
        assert resp.status_code == 422
        assert "description" in resp.json()["detail"]

    def test_from_schema_without_schema_422(self, client: TestClient):
        resp = client.post(
            "/api/v1/presets/preview",
            json={"mode": "from_schema", "sample_html": SAMPLE_HTML},
        )
        assert resp.status_code == 422
        assert "schema" in resp.json()["detail"]
