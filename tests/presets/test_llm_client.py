from __future__ import annotations

import pytest

from src.presets.llm import client as llm_client
from src.presets.llm.client import LLMError, complete, resolve_api_key


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = type("M", (), {"content": content})()


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class TestResolveApiKey:
    def test_openai_prefix(self, mocker):
        s = mocker.patch.object(llm_client, "settings")
        s.openai_api_key.get_secret_value.return_value = "sk-oai"
        assert resolve_api_key("openai/gpt-5.4-mini") == ("sk-oai", None)

    def test_anthropic_prefix(self, mocker):
        s = mocker.patch.object(llm_client, "settings")
        s.anthropic_api_key.get_secret_value.return_value = "sk-ant"
        assert resolve_api_key("anthropic/claude-haiku-4-5") == ("sk-ant", None)

    def test_custom_prefix_returns_base_url(self, mocker):
        s = mocker.patch.object(llm_client, "settings")
        s.custom_llm_api_key.get_secret_value.return_value = "sk-custom"
        s.custom_llm_base_url = "https://scriptrun.ai/v1"
        key, base = resolve_api_key("custom/some-model")
        assert key == "sk-custom"
        assert base == "https://scriptrun.ai/v1"

    def test_unknown_provider_raises(self, mocker):
        mocker.patch.object(llm_client, "settings")
        with pytest.raises(LLMError):
            resolve_api_key("bogusprovider/x")

    def test_missing_key_raises(self, mocker):
        s = mocker.patch.object(llm_client, "settings")
        s.openai_api_key = None
        with pytest.raises(LLMError) as exc:
            resolve_api_key("openai/gpt-5.4-mini")
        assert "OPENAI_API_KEY" in str(exc.value)


class TestComplete:
    @pytest.mark.asyncio
    async def test_returns_message_content(self, mocker):
        s = mocker.patch.object(llm_client, "settings")
        s.openai_api_key.get_secret_value.return_value = "sk-oai"
        acompletion = mocker.patch.object(
            llm_client.litellm,
            "acompletion",
            new=mocker.AsyncMock(return_value=_Resp("hello world")),
        )
        out = await complete(
            "openai/gpt-5.4-mini",
            [{"role": "user", "content": "hi"}],
        )
        assert out == "hello world"
        # api_key threaded through, model passed verbatim
        kwargs = acompletion.await_args.kwargs
        assert kwargs["model"] == "openai/gpt-5.4-mini"
        assert kwargs["api_key"] == "sk-oai"

    @pytest.mark.asyncio
    async def test_passes_timeout_from_settings(self, mocker):
        s = mocker.patch.object(llm_client, "settings")
        s.openai_api_key.get_secret_value.return_value = "sk-oai"
        s.preset_llm_timeout_s = 12.5
        acompletion = mocker.patch.object(
            llm_client.litellm,
            "acompletion",
            new=mocker.AsyncMock(return_value=_Resp("ok")),
        )
        await complete("openai/gpt-5.4-mini", [{"role": "user", "content": "x"}])
        # a hung provider socket must not outlive this bound
        assert acompletion.await_args.kwargs["timeout"] == 12.5

    @pytest.mark.asyncio
    async def test_custom_passes_api_base(self, mocker):
        s = mocker.patch.object(llm_client, "settings")
        s.custom_llm_api_key.get_secret_value.return_value = "sk-c"
        s.custom_llm_base_url = "https://scriptrun.ai/v1"
        acompletion = mocker.patch.object(
            llm_client.litellm,
            "acompletion",
            new=mocker.AsyncMock(return_value=_Resp("ok")),
        )
        await complete("custom/m", [{"role": "user", "content": "x"}])
        assert acompletion.await_args.kwargs["api_base"] == "https://scriptrun.ai/v1"

    @pytest.mark.asyncio
    async def test_litellm_error_wrapped(self, mocker):
        s = mocker.patch.object(llm_client, "settings")
        s.openai_api_key.get_secret_value.return_value = "sk-oai"
        mocker.patch.object(
            llm_client.litellm,
            "acompletion",
            new=mocker.AsyncMock(side_effect=RuntimeError("rate limited")),
        )
        with pytest.raises(LLMError) as exc:
            await complete("openai/gpt-5.4-mini", [{"role": "user", "content": "x"}])
        assert "rate limited" in str(exc.value)

    @pytest.mark.asyncio
    async def test_empty_response_raises(self, mocker):
        s = mocker.patch.object(llm_client, "settings")
        s.openai_api_key.get_secret_value.return_value = "sk-oai"
        mocker.patch.object(
            llm_client.litellm,
            "acompletion",
            new=mocker.AsyncMock(return_value=_Resp(None)),
        )
        with pytest.raises(LLMError):
            await complete("openai/gpt-5.4-mini", [{"role": "user", "content": "x"}])
