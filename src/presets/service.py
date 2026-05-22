"""Business logic for preset management, generation, testing and preview.

All domain exceptions are plain Python exceptions — no HTTPException.
The API layer (src/api/presets.py) is a thin wrapper; the global exception
handlers in src/api/exception_handlers.py map these to HTTP responses.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from src.presets.exceptions import PresetValidationError, SampleFetchError
from src.presets.llm.client import LLMError
from src.presets.llm.schema_gen import infer_schema
from src.presets.llm.selector_gen import generate_selectors
from src.presets.models import ParsingInstructions, Preset
from src.presets.parser_pipeline import run as run_pipeline
from src.presets.requests import (
    PresetGenerateRequest,
    PresetPreviewRequest,
    PresetTestRequest,
)
from src.presets.store import (
    USER_PREFIX,
    PresetAlreadyExists,
    PresetNameInvalid,
    PresetStore,
)
from src.settings import settings

log = logging.getLogger(__name__)

_SAMPLE_FETCH_TIMEOUT_S = 30.0
_MAX_REDIRECTS = 5
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def _assert_public_url(url: str) -> None:
    """Reject SSRF targets. Raises SampleFetchError for any non-public address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SampleFetchError(f"unsupported URL scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise SampleFetchError("URL has no host")

    addrs: list[str] = []
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
            addrs = [info[4][0] for info in infos]
        except OSError as exc:
            raise SampleFetchError(f"cannot resolve host: {host}") from exc

    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        unsafe = (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_reserved,
            ip.is_multicast,
            ip.is_unspecified,
            ip.version == 4 and ip in _CGNAT_NET,
        )
        if any(unsafe):
            raise SampleFetchError(f"refusing to fetch non-public address ({addr})")


async def _fetch_sample_html(url: str) -> str:
    """Fetch a sample page for preset generation/testing.

    Redirects are followed manually so every hop is SSRF-revalidated.
    Raises SampleFetchError on any network/SSRF failure.
    """
    current = url
    try:
        async with httpx.AsyncClient(
            timeout=_SAMPLE_FETCH_TIMEOUT_S, follow_redirects=False
        ) as http:
            for _ in range(_MAX_REDIRECTS + 1):
                _assert_public_url(current)
                resp = await http.get(current, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        break
                    current = str(httpx.URL(current).join(location))
                    continue
                resp.raise_for_status()
                return resp.text
        raise SampleFetchError("too many redirects fetching sample")
    except SampleFetchError:
        raise
    except httpx.HTTPError as exc:
        raise SampleFetchError(f"sample fetch failed: {exc}") from exc


class PresetService:
    def list_presets(
        self,
        kind: Literal["builtin", "user"] | None,
        source: str | None,
        store: PresetStore,
    ) -> list[Preset]:
        return store.list(kind=kind, source=source)

    def list_llm_models(self) -> dict:
        available: list[str] = []
        if settings.openai_api_key:
            available += ["openai/gpt-5.4-mini", "openai/gpt-5.1"]
        if settings.anthropic_api_key:
            available += ["anthropic/claude-haiku-4-5-20251001"]
        if settings.gemini_api_key:
            available += ["gemini/gemini-2.0-flash"]
        if settings.openrouter_api_key:
            available += ["openrouter/auto"]
        if settings.custom_llm_base_url:
            available += ["custom/default"]
        return {"default": settings.default_llm_model, "available": available}

    def get(self, name: str, store: PresetStore) -> Preset:
        return store.get(name)

    def create(self, preset: Preset, store: PresetStore) -> Preset:
        return store.create(preset)

    def update(self, name: str, preset: Preset, store: PresetStore) -> Preset:
        return store.update(name, preset)

    def delete(self, name: str, store: PresetStore) -> None:
        store.delete(name)

    async def _get_sample_html(
        self,
        req: PresetGenerateRequest | PresetTestRequest | PresetPreviewRequest,
    ) -> str:
        if req.sample_html:
            return req.sample_html
        if req.sample_url:
            return await _fetch_sample_html(req.sample_url)
        raise PresetValidationError("sample_url or sample_html is required")

    async def generate(
        self, req: PresetGenerateRequest, store: PresetStore
    ) -> Preset:
        if req.mode == "manual":
            if not req.preset:
                raise PresetValidationError("manual mode requires 'preset'")
            try:
                preset = Preset.model_validate(req.preset)
            except ValidationError as exc:
                raise PresetValidationError(str(exc)) from exc
            return store.create(preset)

        if not req.source:
            raise PresetValidationError(f"{req.mode} mode requires 'source'")

        # Reject doomed names before fetching/LLM so we don't burn tokens.
        if store.builtin.exists(req.name):
            raise PresetAlreadyExists(req.name)
        if not req.name.startswith(USER_PREFIX):
            raise PresetNameInvalid(
                f"user-defined preset name must start with {USER_PREFIX!r}: "
                f"got {req.name!r}"
            )
        if store.user.exists(req.name):
            raise PresetAlreadyExists(req.name)

        html = await self._get_sample_html(req)
        model = req.llm_model or settings.default_llm_model

        try:
            if req.mode == "from_prompt":
                if not req.description:
                    raise PresetValidationError(
                        "from_prompt mode requires 'description'"
                    )
                out_schema = await infer_schema(html, req.description, model)
            else:  # from_schema
                if not req.schema_:
                    raise PresetValidationError("from_schema mode requires 'schema'")
                out_schema = req.schema_
            instructions = await generate_selectors(html, out_schema, model)
        except LLMError as exc:
            log.warning("preset generate LLM call failed model=%s: %s", model, exc)
            raise

        preset = Preset(
            name=req.name,
            source=req.source,
            kind="user",
            request_defaults={},
            locales={},
            default_locale="us",
            parsing_instructions=instructions,
            output_schema=out_schema,
            updated_at=time.time(),
        )
        return store.create(preset)

    async def test(
        self, name: str, req: PresetTestRequest, store: PresetStore
    ) -> dict:
        preset = store.get(name)
        html = await self._get_sample_html(req)
        result = await run_pipeline(
            html,
            preset.parsing_instructions,
            self_heal=req.self_heal,
            llm_model=req.llm_model,
            output_schema=preset.output_schema,
            llm_extract_prompt=preset.llm_extract_prompt,
        )
        return {
            "extracted": result.data,
            "warnings": result.warnings,
            "mode": result.mode,
        }

    async def preview(self, req: PresetPreviewRequest) -> dict:
        html = await self._get_sample_html(req)
        output_schema = req.output_schema
        instructions = None

        if req.parsing_instructions is not None:
            try:
                instructions = ParsingInstructions.model_validate(
                    req.parsing_instructions
                )
            except ValidationError as exc:
                raise PresetValidationError(str(exc)) from exc
        else:
            if req.mode == "manual":
                raise PresetValidationError(
                    "manual mode requires 'parsing_instructions'"
                )
            model = req.llm_model or settings.default_llm_model
            try:
                if req.mode == "from_prompt":
                    if not req.description:
                        raise PresetValidationError(
                            "from_prompt mode requires 'description'"
                        )
                    output_schema = await infer_schema(html, req.description, model)
                else:  # from_schema
                    if not req.schema_:
                        raise PresetValidationError(
                            "from_schema mode requires 'schema'"
                        )
                    output_schema = req.schema_
                instructions = await generate_selectors(html, output_schema, model)
            except LLMError as exc:
                log.warning(
                    "preset preview LLM call failed model=%s: %s", model, exc
                )
                raise

        result = await run_pipeline(
            html,
            instructions,
            self_heal=req.self_heal,
            llm_model=req.llm_model,
            output_schema=output_schema,
            llm_extract_prompt=req.llm_extract_prompt,
        )
        return {
            "parsing_instructions": (
                instructions.model_dump(mode="json") if instructions else None
            ),
            "output_schema": output_schema,
            "extracted": result.data,
            "warnings": result.warnings,
            "mode": result.mode,
        }


preset_service = PresetService()
