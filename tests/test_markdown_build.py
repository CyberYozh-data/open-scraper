from __future__ import annotations

import pytest

from src.markdown_build import build_markdown_outputs, resolve_formats
from src.schemas import MarkdownOptions

ARTICLE = (
    "<html><body><article><h1>Title</h1>"
    "<p>This is the substantial real article body that readers care about.</p>"
    "</article>"
    '<nav><a href="/a">Home</a><a href="/b">About</a><a href="/c">More</a></nav>'
    "</body></html>"
)


class TestResolveFormats:
    def test_none_uses_legacy_booleans(self):
        assert resolve_formats(None, raw_html=True, screenshot=False) == {"raw_html"}
        assert resolve_formats(None, raw_html=False, screenshot=True) == {"screenshot"}
        assert resolve_formats(None, raw_html=False, screenshot=False) == set()

    def test_list_unions_with_booleans(self):
        out = resolve_formats(["markdown"], raw_html=True, screenshot=False)
        assert out == {"markdown", "raw_html"}

    def test_raw_html_and_screenshot_via_formats_list(self):
        # Listing them in `formats` must be honoured even when the legacy
        # booleans are off (the documented union contract).
        out = resolve_formats(["raw_html", "screenshot"], raw_html=False, screenshot=False)
        assert out == {"raw_html", "screenshot"}


class TestBuildMarkdownOutputs:
    @pytest.mark.asyncio
    async def test_markdown_only(self):
        out, warnings = await build_markdown_outputs(
            ARTICLE, base_url="https://x.com", fmts={"markdown"}, opts=MarkdownOptions()
        )
        assert "# Title" in out["markdown"]
        assert "fit_markdown" not in out
        assert warnings == []

    @pytest.mark.asyncio
    async def test_fit_markdown_defaults_to_pruning_with_warning(self):
        out, warnings = await build_markdown_outputs(
            ARTICLE,
            base_url="https://x.com",
            fmts={"fit_markdown"},
            opts=MarkdownOptions(),  # content_filter="none"
        )
        assert "real article body" in out["fit_markdown"]
        assert "About" not in out["fit_markdown"]
        assert any("pruning" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_fit_markdown_llm(self, mocker):
        import src.markdown_build as mb

        mocker.patch.object(
            mb,
            "llm_markdown_filter",
            new=mocker.AsyncMock(return_value="# Clean"),
        )
        out, warnings = await build_markdown_outputs(
            ARTICLE,
            base_url="https://x.com",
            fmts={"fit_markdown"},
            opts=MarkdownOptions(content_filter="llm"),
        )
        assert out["fit_markdown"] == "# Clean"
        assert warnings == []

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_pruning(self, mocker):
        import src.markdown_build as mb
        from src.presets.llm.client import LLMError

        mocker.patch.object(
            mb,
            "llm_markdown_filter",
            new=mocker.AsyncMock(side_effect=LLMError("boom")),
        )
        out, warnings = await build_markdown_outputs(
            ARTICLE,
            base_url="https://x.com",
            fmts={"fit_markdown"},
            opts=MarkdownOptions(content_filter="llm"),
        )
        assert "real article body" in out["fit_markdown"]
        assert any("llm" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_citations_populate_references(self):
        out, _ = await build_markdown_outputs(
            ARTICLE,
            base_url="https://x.com",
            fmts={"markdown"},
            opts=MarkdownOptions(citations=True),
        )
        assert "⟨1⟩" in out["markdown"]
        assert out["markdown_references"].startswith("⟨1⟩ ")

    @pytest.mark.asyncio
    async def test_links_and_html_formats(self):
        out, _ = await build_markdown_outputs(
            ARTICLE, base_url="https://x.com", fmts={"links", "html"}, opts=MarkdownOptions()
        )
        assert out["links"] == ["https://x.com/a", "https://x.com/b", "https://x.com/c"]
        assert "<article" in out["html"]


class TestApplyBridge:
    @pytest.mark.asyncio
    async def test_noop_for_legacy_request(self):
        from src.markdown_build import apply

        out, warnings = await apply(
            {"url": "https://x.com", "raw_html": True}, ARTICLE, base_url="https://x.com"
        )
        assert out == {}
        assert warnings == []

    @pytest.mark.asyncio
    async def test_builds_requested_formats(self):
        from src.markdown_build import apply

        out, _ = await apply(
            {"url": "https://x.com", "formats": ["markdown"], "markdown_options": {}},
            ARTICLE,
            base_url="https://x.com",
        )
        assert "# Title" in out["markdown"]

    @pytest.mark.asyncio
    async def test_empty_html_is_noop(self):
        from src.markdown_build import apply

        out, warnings = await apply(
            {"url": "https://x.com", "formats": ["markdown"]}, "", base_url="https://x.com"
        )
        assert out == {} and warnings == []

    @pytest.mark.asyncio
    async def test_unexpected_error_degrades_to_warning(self, mocker):
        import src.markdown_build as mb

        mocker.patch.object(
            mb,
            "build_markdown_outputs",
            new=mocker.AsyncMock(side_effect=RuntimeError("boom")),
        )
        out, warnings = await mb.apply(
            {"url": "https://x.com", "formats": ["markdown"]},
            ARTICLE,
            base_url="https://x.com",
        )
        assert out == {}
        assert any("markdown generation failed" in w for w in warnings)
