from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.presets.service as service_mod
from src.api.presets import get_preset_store, router as presets_router
from src.presets.models import LocaleProfile, ParsingInstructions, Preset
from src.extract.models import FieldRule
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
        url_template="https://www.amazon.{domain}/dp/{asin}",
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


class TestLlmModels:
    def test_lists_models_for_configured_keys(self, client: TestClient, mocker):
        s = mocker.patch.object(service_mod, "settings")
        s.openai_api_key = "x"
        s.anthropic_api_key = None
        s.gemini_api_key = None
        s.openrouter_api_key = None
        s.custom_llm_base_url = None
        s.default_llm_model = "openai/gpt-5.4-mini"
        resp = client.get("/api/v1/presets/llm-models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default"] == "openai/gpt-5.4-mini"
        assert any(m.startswith("openai/") for m in body["available"])
        assert not any(m.startswith("anthropic/") for m in body["available"])

    def test_custom_endpoint_listed_when_base_url_set(
        self, client: TestClient, mocker
    ):
        s = mocker.patch.object(service_mod, "settings")
        s.openai_api_key = None
        s.anthropic_api_key = None
        s.gemini_api_key = None
        s.openrouter_api_key = None
        s.custom_llm_base_url = "https://scriptrun.ai/v1"
        s.default_llm_model = "openai/gpt-5.4-mini"
        body = client.get("/api/v1/presets/llm-models").json()
        assert any(m.startswith("custom/") for m in body["available"])


class TestGenerateManual:
    def test_manual_mode_is_create(self, client: TestClient):
        payload = {
            "name": "user_manual",
            "mode": "manual",
            "preset": {
                "name": "user_manual",
                "source": "custom",
                "request_defaults": {"device": "desktop"},
                "locales": {"us": {"domain": "com", "country": "US"}},
                "default_locale": "us",
                "updated_at": 1.0,
            },
        }
        resp = client.post("/api/v1/presets/generate", json=payload)
        assert resp.status_code == 201
        assert resp.json()["name"] == "user_manual"
        assert client.get("/api/v1/presets/user_manual").status_code == 200


class TestGenerateFromSchema:
    def test_from_schema_generates_selectors(self, client: TestClient, mocker):
        mocker.patch.object(
            service_mod,
            "_fetch_sample_html",
            new=mocker.AsyncMock(return_value=SAMPLE_HTML),
        )
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
        payload = {
            "name": "user_fromschema",
            "mode": "from_schema",
            "source": "custom",
            "sample_url": "https://example.com/p",
            "schema": {"title": "string"},
        }
        resp = client.post("/api/v1/presets/generate", json=payload)
        assert resp.status_code == 201
        saved = client.get("/api/v1/presets/user_fromschema").json()
        assert saved["parsing_instructions"]["fields"]["title"]["selector"] == "#t"
        assert saved["kind"] == "user"

    def test_from_schema_llm_failure_502(self, client: TestClient, mocker):
        from src.presets.llm.client import LLMError

        mocker.patch.object(
            service_mod,
            "_fetch_sample_html",
            new=mocker.AsyncMock(return_value=SAMPLE_HTML),
        )
        mocker.patch.object(
            service_mod,
            "generate_selectors",
            new=mocker.AsyncMock(side_effect=LLMError("model down")),
        )
        payload = {
            "name": "user_x",
            "mode": "from_schema",
            "source": "custom",
            "sample_url": "https://example.com/p",
            "schema": {"title": "string"},
        }
        resp = client.post("/api/v1/presets/generate", json=payload)
        assert resp.status_code == 502


class TestGenerateFromPrompt:
    def test_from_prompt_infers_schema_then_selectors(
        self, client: TestClient, mocker
    ):
        mocker.patch.object(
            service_mod,
            "_fetch_sample_html",
            new=mocker.AsyncMock(return_value=SAMPLE_HTML),
        )
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
        payload = {
            "name": "user_fromprompt",
            "mode": "from_prompt",
            "source": "custom",
            "sample_url": "https://example.com/p",
            "description": "grab the title",
        }
        resp = client.post("/api/v1/presets/generate", json=payload)
        assert resp.status_code == 201
        saved = client.get("/api/v1/presets/user_fromprompt").json()
        assert saved["output_schema"] == {"title": {"type": "string"}}


class TestGenerateValidation:
    def test_non_user_prefix_rejected(self, client: TestClient):
        payload = {
            "name": "amazon_product",
            "mode": "from_schema",
            "source": "amazon",
            "sample_url": "https://x.com",
            "schema": {"a": "string"},
        }
        resp = client.post("/api/v1/presets/generate", json=payload)
        assert resp.status_code in (400, 409)

    def test_unknown_mode_422(self, client: TestClient):
        resp = client.post(
            "/api/v1/presets/generate",
            json={"name": "user_z", "mode": "telepathy"},
        )
        assert resp.status_code == 422

    def test_from_schema_garbage_schema_422_before_llm(
        self, client: TestClient, mocker
    ):
        gen = mocker.patch.object(
            service_mod, "generate_selectors", new=mocker.AsyncMock()
        )
        fetch = mocker.patch.object(
            service_mod, "_fetch_sample_html", new=mocker.AsyncMock()
        )
        resp = client.post(
            "/api/v1/presets/generate",
            json={
                "name": "user_bad_schema",
                "mode": "from_schema",
                "source": "custom",
                "sample_url": "https://example.com",
                "schema": ["not", "an", "object"],
            },
        )
        assert resp.status_code == 422
        # rejected before spending tokens or even fetching
        gen.assert_not_called()
        fetch.assert_not_called()

    def test_from_schema_empty_schema_422(self, client: TestClient, mocker):
        mocker.patch.object(
            service_mod, "generate_selectors", new=mocker.AsyncMock()
        )
        resp = client.post(
            "/api/v1/presets/generate",
            json={
                "name": "user_empty_schema",
                "mode": "from_schema",
                "source": "custom",
                "sample_url": "https://example.com",
                "schema": {},
            },
        )
        assert resp.status_code == 422


class TestPresetTest:
    def test_dry_run_returns_extracted(self, client: TestClient, mocker):
        mocker.patch.object(
            service_mod,
            "_fetch_sample_html",
            new=mocker.AsyncMock(return_value=SAMPLE_HTML),
        )
        resp = client.post(
            "/api/v1/presets/amazon_product/test",
            json={"sample_url": "https://www.amazon.com/dp/X"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["extracted"]["title"] == "Widget"
        assert body["mode"] == "deterministic"

    def test_test_unknown_preset_404(self, client: TestClient, mocker):
        mocker.patch.object(
            service_mod,
            "_fetch_sample_html",
            new=mocker.AsyncMock(return_value=SAMPLE_HTML),
        )
        resp = client.post(
            "/api/v1/presets/ghost/test",
            json={"sample_url": "https://x.com"},
        )
        assert resp.status_code == 404

    def test_test_accepts_inline_sample_html(self, client: TestClient):
        resp = client.post(
            "/api/v1/presets/amazon_product/test",
            json={"sample_html": SAMPLE_HTML},
        )
        assert resp.status_code == 200
        assert resp.json()["extracted"]["title"] == "Widget"
