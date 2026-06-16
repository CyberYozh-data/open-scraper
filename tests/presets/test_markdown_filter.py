from __future__ import annotations

import pytest

from src.presets.llm import markdown_filter as mf


class TestLlmMarkdownFilter:
    @pytest.mark.asyncio
    async def test_extracts_content_tag(self, mocker):
        mocker.patch.object(
            mf,
            "complete",
            new=mocker.AsyncMock(
                return_value="<content># Title\n\nReal body</content>"
            ),
        )
        out = await mf.llm_markdown_filter(
            "<h1>Title</h1><nav>junk</nav>", model="openai/gpt-5.4-mini"
        )
        assert out == "# Title\n\nReal body"

    @pytest.mark.asyncio
    async def test_falls_back_when_no_content_tag(self, mocker):
        mocker.patch.object(
            mf, "complete", new=mocker.AsyncMock(return_value="# Bare markdown")
        )
        out = await mf.llm_markdown_filter("<h1>x</h1>", model="openai/gpt-5.4-mini")
        assert out == "# Bare markdown"

    @pytest.mark.asyncio
    async def test_instruction_passed_into_prompt(self, mocker):
        spy = mocker.AsyncMock(return_value="<content>ok</content>")
        mocker.patch.object(mf, "complete", new=spy)
        await mf.llm_markdown_filter(
            "<h1>x</h1>",
            model="openai/gpt-5.4-mini",
            instruction="keep only prices",
        )
        sent = spy.call_args.args[1]  # messages
        joined = " ".join(m["content"] for m in sent)
        assert "keep only prices" in joined
