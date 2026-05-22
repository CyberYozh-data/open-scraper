from __future__ import annotations

import pytest

from src.presets.llm import extract as extract_mod
from src.presets.llm import schema_gen as schema_mod
from src.presets.llm import selector_gen as selgen_mod
from src.presets.llm.htmlclean import clean_html
from src.presets.models import ParsingInstructions


SAMPLE = """
<html><head><title>t</title>
<style>.x{color:red}</style>
<script>var a=1;</script>
</head><body>
  <h1 id="t">Widget</h1>
  <span class="price">$9.99</span>
  <svg><path d="M0 0"/></svg>
  <noscript>no js</noscript>
  <p>   lots    of     space   </p>
</body></html>
"""


class TestCleanHtml:
    def test_strips_script_style_svg_noscript(self):
        out = clean_html(SAMPLE)
        assert "var a=1" not in out
        assert "color:red" not in out
        assert "<svg" not in out
        assert "no js" not in out
        assert "Widget" in out

    def test_collapses_whitespace(self):
        out = clean_html(SAMPLE)
        assert "lots of space" in out

    def test_truncates_to_max_chars(self):
        out = clean_html("<p>" + "a" * 5000 + "</p>", max_chars=1000)
        assert len(out) <= 1000


class TestSelectorGen:
    @pytest.mark.asyncio
    async def test_returns_parsing_instructions(self, mocker):
        mocker.patch.object(
            selgen_mod,
            "complete",
            new=mocker.AsyncMock(
                return_value='```json\n'
                '{"type":"css","fields":{"title":{"selector":"#t"},'
                '"price":{"selector":".price","post_process":'
                '[{"op":"parse_price","args":["us"]}]}}}\n```'
            ),
        )
        instr = await selgen_mod.generate_selectors(
            SAMPLE,
            schema={"title": "string", "price": "number"},
            model="openai/gpt-5.4-mini",
        )
        assert isinstance(instr, ParsingInstructions)
        assert instr.fields["title"].selector == "#t"
        assert instr.fields["price"].post_process[0].op == "parse_price"

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self, mocker):
        mocker.patch.object(
            selgen_mod,
            "complete",
            new=mocker.AsyncMock(return_value="not json at all"),
        )
        with pytest.raises(selgen_mod.LLMError):
            await selgen_mod.generate_selectors(
                SAMPLE, schema={"x": "string"}, model="openai/gpt-5.4-mini"
            )

    @pytest.mark.asyncio
    async def test_schema_not_matching_parsing_instructions_raises(self, mocker):
        mocker.patch.object(
            selgen_mod,
            "complete",
            new=mocker.AsyncMock(return_value='{"totally":"wrong-shape"}'),
        )
        with pytest.raises(selgen_mod.LLMError):
            await selgen_mod.generate_selectors(
                SAMPLE, schema={"x": "string"}, model="openai/gpt-5.4-mini"
            )


class TestLlmExtract:
    @pytest.mark.asyncio
    async def test_extracts_json_object(self, mocker):
        mocker.patch.object(
            extract_mod,
            "complete",
            new=mocker.AsyncMock(
                return_value='{"title":"Widget","price":9.99}'
            ),
        )
        data = await extract_mod.llm_extract(
            SAMPLE,
            schema={"title": "string", "price": "number"},
            prompt=None,
            model="openai/gpt-5.4-mini",
        )
        assert data == {"title": "Widget", "price": 9.99}

    @pytest.mark.asyncio
    async def test_prompt_only_mode(self, mocker):
        spy = mocker.AsyncMock(return_value='{"summary":"ok"}')
        mocker.patch.object(extract_mod, "complete", new=spy)
        data = await extract_mod.llm_extract(
            SAMPLE, schema=None, prompt="Summarize the page", model="m"
        )
        assert data == {"summary": "ok"}
        # the natural-language instruction reaches the model
        sent = spy.await_args.args[1]
        assert any("Summarize the page" in m["content"] for m in sent)

    @pytest.mark.asyncio
    async def test_bad_json_raises(self, mocker):
        mocker.patch.object(
            extract_mod, "complete", new=mocker.AsyncMock(return_value="<oops>")
        )
        with pytest.raises(extract_mod.LLMError):
            await extract_mod.llm_extract(
                SAMPLE, schema={"x": "string"}, prompt=None, model="m"
            )


class TestSchemaGen:
    @pytest.mark.asyncio
    async def test_infers_schema_from_description(self, mocker):
        mocker.patch.object(
            schema_mod,
            "complete",
            new=mocker.AsyncMock(
                return_value='{"title":{"type":"string"},'
                '"price":{"type":"number"}}'
            ),
        )
        schema = await schema_mod.infer_schema(
            SAMPLE,
            description="grab the product title and price",
            model="openai/gpt-5.4-mini",
        )
        assert "title" in schema
        assert schema["price"]["type"] == "number"

    @pytest.mark.asyncio
    async def test_non_object_raises(self, mocker):
        mocker.patch.object(
            schema_mod, "complete", new=mocker.AsyncMock(return_value="[1,2,3]")
        )
        with pytest.raises(schema_mod.LLMError):
            await schema_mod.infer_schema(
                SAMPLE, description="x", model="m"
            )
