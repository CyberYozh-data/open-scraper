"""Fix-round-1 finding 4: `PresetService.test()`/`.preview()` predict what a
real materialize()-driven scrape would return, so a preset author validating
through these endpoints must not see relative urls for a preset that will
resolve absolute in production.

Both call `run_pipeline` directly (no `materialize()` in the loop -- there is
no locale/domain to materialize against, since a test/preview call operates
on an arbitrary sample page), but `req.sample_url` names exactly the URL the
sample was fetched from, which is precisely what `urljoin` needs as its base.
`sample_html` is supplied alongside `sample_url` in every test below so
`_get_sample_html` short-circuits to it and no real network fetch happens.
"""
from __future__ import annotations

import json

import pytest

from src.extract.models import FieldRule, PostProcess
from src.presets.models import LocaleProfile, ParsingInstructions, Preset
from src.presets.requests import PresetPreviewRequest, PresetTestRequest
from src.presets.service import PresetService
from src.presets.store import FilePresetStore, PresetStore

HTML = '<a id="p" href="/dp/B0F8L98RLY/ref=sr_1">Product</a>'


def _preset(**over):
    base = dict(
        name="user_urljoin_test",
        source="custom",
        kind="user",
        url_template="https://e.com/{x}",
        request_defaults={},
        locales={"us": LocaleProfile(domain="com", country="US")},
        default_locale="us",
        parsing_instructions=ParsingInstructions(
            type="css",
            fields={
                "url": FieldRule(
                    selector="#p", attr="href",
                    post_process=[PostProcess(op="urljoin")],
                ),
            },
        ),
        version=1,
        updated_at=1.0,
    )
    base.update(over)
    return Preset(**base)


@pytest.mark.asyncio
class TestPresetTestInjectsSampleUrlAsUrljoinBase:
    async def test_sample_url_resolves_urljoin_absolute(self, tmp_path):
        store = PresetStore(user=FilePresetStore(base_path=tmp_path / "u"))
        store.create(_preset())
        svc = PresetService()

        result = await svc.test(
            "user_urljoin_test",
            PresetTestRequest(
                sample_html=HTML, sample_url="https://www.amazon.de/s?k=laptop"
            ),
            store,
        )

        assert result["extracted"]["url"] == "https://www.amazon.de/dp/B0F8L98RLY/ref=sr_1"

    async def test_sample_html_only_leaves_it_relative_and_warns(self, tmp_path):
        """No sample_url at all -> no base to inject -- the same no-op-and-warn
        behaviour as any other unmaterialized call (see
        tests/extract/test_post_process.py::TestUrljoinOp)."""
        store = PresetStore(user=FilePresetStore(base_path=tmp_path / "u"))
        store.create(_preset())
        svc = PresetService()

        result = await svc.test(
            "user_urljoin_test",
            PresetTestRequest(sample_html=HTML),
            store,
        )

        assert result["extracted"]["url"] == "/dp/B0F8L98RLY/ref=sr_1"
        assert any("urljoin" in w for w in result["warnings"])


@pytest.mark.asyncio
class TestPresetPreviewInjectsSampleUrlAsUrljoinBase:
    async def test_manual_preview_resolves_urljoin_absolute(self):
        svc = PresetService()
        preset = _preset()

        result = await svc.preview(
            PresetPreviewRequest(
                mode="manual",
                sample_html=HTML,
                sample_url="https://www.amazon.co.uk/s?k=laptop",
                parsing_instructions=preset.parsing_instructions.model_dump(mode="json"),
            )
        )

        assert result["extracted"]["url"] == "https://www.amazon.co.uk/dp/B0F8L98RLY/ref=sr_1"

    async def test_the_injected_base_does_not_leak_into_the_returned_instructions(self):
        """Fix-round-2 finding 1: `parsing_instructions` in the response is
        returned precisely so a caller can persist it -- the only way to
        retrieve LLM-generated instructions in from_prompt/from_schema mode.
        `extracted` must reflect the injected base (proven above); the
        returned `parsing_instructions` must NOT -- echoing it back re-opens
        exactly the bug fixed for self-heal persistence last round: a
        preview -> create round-trip would freeze this one request's
        sample_url into the stored preset forever."""
        svc = PresetService()
        preset = _preset()

        result = await svc.preview(
            PresetPreviewRequest(
                mode="manual",
                sample_html=HTML,
                sample_url="https://www.amazon.co.uk/s?k=laptop",
                parsing_instructions=preset.parsing_instructions.model_dump(mode="json"),
            )
        )

        returned_args = result["parsing_instructions"]["fields"]["url"]["post_process"][0]["args"]
        assert returned_args == [], (
            "the materializer-injected sample_url leaked into the "
            f"persistable parsing_instructions: {returned_args!r}"
        )
        # extracted still reflects the injection -- only the RETURNED
        # instructions must stay clean.
        assert result["extracted"]["url"] == "https://www.amazon.co.uk/dp/B0F8L98RLY/ref=sr_1"

    async def test_a_credential_in_sample_url_does_not_reach_the_response(self):
        """Verbatim reproduction of the review's repro: a sample_url query
        string carrying a credential must never appear anywhere in the
        response `preview()` returns -- that response is exactly what
        `GET /api/v1/presets/{name}` would later serve, unauthenticated, if
        a caller persisted it."""
        svc = PresetService()
        preset = _preset()
        secret_url = "https://shop.example.com/search?api_key=SUPERSECRET123&q=laptop"

        result = await svc.preview(
            PresetPreviewRequest(
                mode="manual",
                sample_html=HTML,
                sample_url=secret_url,
                parsing_instructions=preset.parsing_instructions.model_dump(mode="json"),
            )
        )

        assert "SUPERSECRET123" not in json.dumps(result["parsing_instructions"])
        # The extraction itself is still correct -- resolved against the
        # secret-bearing sample_url, just not echoing that URL back.
        assert result["extracted"]["url"] == "https://shop.example.com/dp/B0F8L98RLY/ref=sr_1"
