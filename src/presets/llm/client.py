"""Thin async wrapper over litellm.

Keys are server-side (like CYBERYOZH_API_KEY): callers never send their own.
The provider is resolved from the model prefix (`openai/`, `anthropic/`,
`gemini/`, `openrouter/`, `custom/`) and the matching key is read from
settings and threaded explicitly into litellm — we don't rely on litellm
scraping os.environ, because Settings loads .env into an object, not the
process environment.
"""
from __future__ import annotations

import logging

import litellm

from src.settings import settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """LLM call failed or is misconfigured (missing key, unknown provider,
    empty response, upstream error). API maps this to 502/400 as appropriate."""


def resolve_api_key(model: str) -> tuple[str, str | None]:
    """Return (api_key, api_base) for a prefixed model string.

    api_base is None for first-party providers and the configured custom
    endpoint for `custom/*`. Raises LLMError on unknown provider or missing
    key so a misconfig surfaces clearly instead of as an opaque 401 later.
    """
    provider = model.split("/", 1)[0] if "/" in model else ""
    key_map = {
        "openai": ("openai_api_key", "OPENAI_API_KEY"),
        "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        "gemini": ("gemini_api_key", "GEMINI_API_KEY"),
        "openrouter": ("openrouter_api_key", "OPENROUTER_API_KEY"),
        "custom": ("custom_llm_api_key", "CUSTOM_LLM_API_KEY"),
    }
    if provider not in key_map:
        raise LLMError(
            f"unknown LLM provider {provider!r} in model {model!r}; "
            f"expected one of {sorted(key_map)}"
        )
    attr, env_name = key_map[provider]
    secret = getattr(settings, attr, None)
    if secret is None:
        raise LLMError(
            f"{env_name} is not set; required for model {model!r}"
        )
    api_key = secret.get_secret_value()
    api_base = settings.custom_llm_base_url if provider == "custom" else None
    return api_key, api_base


async def complete(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> str:
    api_key, api_base = resolve_api_key(model)
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens or settings.preset_llm_max_tokens,
        "api_key": api_key,
        # Bound the call so a hung provider socket can't outlive the job
        # timeout and wedge an in-worker self-heal/extract round-trip.
        "timeout": settings.preset_llm_timeout_s,
    }
    if api_base:
        kwargs["api_base"] = api_base

    try:
        resp = await litellm.acompletion(**kwargs)
    except LLMError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise LLMError(f"LLM call failed for {model!r}: {exc}") from exc

    try:
        content = resp.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise LLMError(f"malformed LLM response for {model!r}") from exc
    if not content:
        raise LLMError(f"empty LLM response for {model!r}")
    return content
