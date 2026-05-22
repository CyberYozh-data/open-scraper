from __future__ import annotations

import pytest

from src.extract.models import FieldRule
from src.presets.models import ParsingInstructions
from src.presets import parser_pipeline as pp


HTML_OK = "<html><body><h1 id='t'>Widget</h1><span class='p'>$9.99</span></body></html>"
HTML_CHANGED = "<html><body><h2 class='new-title'>Widget</h2></body></html>"


def _instr(selector: str = "#t") -> ParsingInstructions:
    return ParsingInstructions(
        type="css",
        fields={"title": FieldRule(selector=selector, required=True)},
    )


class TestDeterministic:
    @pytest.mark.asyncio
    async def test_happy_path_no_llm(self, mocker):
        spy = mocker.patch.object(pp, "generate_selectors")
        result = await pp.run(
            HTML_OK,
            instructions=_instr("#t"),
            self_heal=True,
            llm_model="openai/gpt-5.4-mini",
            output_schema=None,
            llm_extract_prompt=None,
        )
        assert result.mode == "deterministic"
        assert result.data["title"] == "Widget"
        assert result.healed_instructions is None
        spy.assert_not_called()  # LLM never touched when selectors work


class TestSelfHeal:
    @pytest.mark.asyncio
    async def test_regenerates_when_required_missing(self, mocker):
        healed = _instr(".new-title")
        mocker.patch.object(
            pp, "generate_selectors", new=mocker.AsyncMock(return_value=healed)
        )
        result = await pp.run(
            HTML_CHANGED,
            instructions=_instr("#t"),  # stale selector, matches nothing
            self_heal=True,
            llm_model="openai/gpt-5.4-mini",
            output_schema=None,
            llm_extract_prompt=None,
        )
        assert result.mode == "self_healed"
        assert result.data["title"] == "Widget"
        assert result.healed_instructions is healed
        assert any("self_healed" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_no_self_heal_when_disabled(self, mocker):
        spy = mocker.patch.object(pp, "generate_selectors")
        result = await pp.run(
            HTML_CHANGED,
            instructions=_instr("#t"),
            self_heal=False,
            llm_model="openai/gpt-5.4-mini",
            output_schema=None,
            llm_extract_prompt=None,
        )
        assert result.mode == "deterministic"
        # required field missing -> warning, but no LLM
        assert result.data["title"] is None
        spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_self_heal_without_model(self, mocker):
        spy = mocker.patch.object(pp, "generate_selectors")
        result = await pp.run(
            HTML_CHANGED,
            instructions=_instr("#t"),
            self_heal=True,
            llm_model=None,  # LLM disabled
            output_schema=None,
            llm_extract_prompt=None,
        )
        assert result.mode == "deterministic"
        spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_heal_still_failing_returns_partial_deterministic(
        self, mocker
    ):
        # healed selectors also miss and there's no AI-only fallback: the
        # caller still gets the (incomplete) deterministic data + warnings,
        # not a null result.
        mocker.patch.object(
            pp,
            "generate_selectors",
            new=mocker.AsyncMock(return_value=_instr("#still-wrong")),
        )
        result = await pp.run(
            HTML_CHANGED,
            instructions=_instr("#t"),
            self_heal=True,
            llm_model="m",
            output_schema=None,
            llm_extract_prompt=None,
        )
        assert result.mode == "deterministic"
        assert result.healed_instructions is None
        assert any("self_heal_did_not_recover" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_llm_error_during_heal_is_swallowed(self, mocker):
        from src.presets.llm.client import LLMError

        mocker.patch.object(
            pp,
            "generate_selectors",
            new=mocker.AsyncMock(side_effect=LLMError("boom")),
        )
        result = await pp.run(
            HTML_CHANGED,
            instructions=_instr("#t"),
            self_heal=True,
            llm_model="m",
            output_schema=None,
            llm_extract_prompt=None,
        )
        assert result.mode == "deterministic"
        assert any("self_heal_failed" in w for w in result.warnings)


class TestAiOnly:
    @pytest.mark.asyncio
    async def test_ai_only_when_no_instructions(self, mocker):
        mocker.patch.object(
            pp,
            "llm_extract",
            new=mocker.AsyncMock(return_value={"title": "Widget"}),
        )
        result = await pp.run(
            HTML_OK,
            instructions=None,
            self_heal=False,
            llm_model="m",
            output_schema={"title": "string"},
            llm_extract_prompt=None,
        )
        assert result.mode == "llm_extracted"
        assert result.data == {"title": "Widget"}

    @pytest.mark.asyncio
    async def test_ai_only_prompt_mode(self, mocker):
        mocker.patch.object(
            pp,
            "llm_extract",
            new=mocker.AsyncMock(return_value={"summary": "x"}),
        )
        result = await pp.run(
            HTML_OK,
            instructions=None,
            self_heal=False,
            llm_model="m",
            output_schema=None,
            llm_extract_prompt="summarize",
        )
        assert result.mode == "llm_extracted"

    @pytest.mark.asyncio
    async def test_no_parser_at_all(self):
        result = await pp.run(
            HTML_OK,
            instructions=None,
            self_heal=False,
            llm_model=None,
            output_schema=None,
            llm_extract_prompt=None,
        )
        assert result.mode == "none"
        assert result.data is None

    @pytest.mark.asyncio
    async def test_ai_only_llm_error_returns_none_mode(self, mocker):
        from src.presets.llm.client import LLMError

        mocker.patch.object(
            pp, "llm_extract", new=mocker.AsyncMock(side_effect=LLMError("x"))
        )
        result = await pp.run(
            HTML_OK,
            instructions=None,
            self_heal=False,
            llm_model="m",
            output_schema={"title": "string"},
            llm_extract_prompt=None,
        )
        assert result.mode == "none"
        assert any("llm_extract_failed" in w for w in result.warnings)
