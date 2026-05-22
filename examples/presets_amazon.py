#!/usr/bin/env python3
"""
Example: Scrape with a built-in preset

Instead of hand-assembling proxy/device/wait/selectors per site, point at a
named preset and pass only the bits that vary (the ASIN and locale). The
server materializes the full request, runs the deterministic parser, and —
if you pass an `llm` model — self-heals selectors when Amazon's markup
drifts.

Requirements:
  - CYBERYOZH_API_KEY in .env (Amazon needs a residential proxy)
  - Optionally a server-side LLM key (OPENAI_API_KEY / ANTHROPIC_API_KEY /
    ...) to enable self-heal — omit `llm` to run the deterministic parser
    alone.
"""
import os
import time

import httpx
from dotenv import load_dotenv
from client_helpers import API_BASE, console, print_result

load_dotenv()


def scrape_amazon_preset(asin: str, locale: str = "us") -> dict:
    payload = {
        "source": "amazon_product",
        "preset_params": {"asin": asin},
        "locale": locale,
    }
    # Enable self-heal only if some server-side LLM key is configured. The
    # server resolves the provider from the model prefix; any of these keys
    # (or a custom OpenAI-compatible endpoint) makes a call possible.
    _llm_keys = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "CUSTOM_LLM_API_KEY",
    )
    if any(os.getenv(k) for k in _llm_keys):
        payload["llm"] = {"model": os.getenv("DEFAULT_LLM_MODEL", "openai/gpt-5.4-mini")}

    console.print(f"[bold cyan]Preset amazon_product[/bold cyan] asin={asin} locale={locale}\n")

    with httpx.Client(timeout=90.0) as client:
        resp = client.post(f"{API_BASE}/scrape/preset/page", json=payload)
        resp.raise_for_status()
        job_id = resp.json()["job_id"]

        deadline = time.time() + 120
        while time.time() < deadline:
            r = client.get(f"{API_BASE}/scrape/{job_id}/results")
            r.raise_for_status()
            body = r.json()
            if body["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(2)

    items = body.get("results") or []
    if not items:
        raise RuntimeError(f"no results: status={body.get('status')} error={body.get('error')}")

    result = items[0]
    applied = result["meta"].get("applied_preset")
    if applied:
        console.print(
            f"[dim]applied preset={applied['name']} v{applied['version']} "
            f"locale={applied.get('locale')}[/dim]"
        )
    print_result(result)
    return result.get("data") or {}


def main():
    if not os.getenv("CYBERYOZH_API_KEY"):
        console.print("[red]⚠ CYBERYOZH_API_KEY not set — Amazon needs a residential proxy[/red]\n")
    scrape_amazon_preset("B08N5WRWNW", locale="us")


if __name__ == "__main__":
    main()
