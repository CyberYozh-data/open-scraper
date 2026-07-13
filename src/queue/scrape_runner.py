"""One page scrape: attempt loop + proxy rotation included.

This is the body of the old worker_pool._worker_main per-page path, moved
verbatim (zero logic changes). Transport-agnostic: give it a runner and a
request dict, get the result envelope back — the same dict the old code put
on the multiprocessing result queue, minus the "job_id" key.
"""
from __future__ import annotations

import logging
import time
import traceback
from typing import Any

from src.browser.runner import (
    HTTP_BAN_STATUSES,
    HTTP_TRANSIENT_STATUSES,
    FetchResult,
    PlaywrightRunner,
)
from src.markdown_build import apply as apply_markdown, resolve_formats
from src.presets.worker_parse import apply as apply_parser
from src.proxy.base import ProxyConfigError, ProxyFailure
from src.proxy.resolver import proxy_resolver
from src.settings import settings

log = logging.getLogger(__name__)

# HTTP status codes that indicate a proxy-related failure. Canonical sets live
# in browser.runner (the runner uses them to mark such a fetch not-ok); here we
# reuse them so the retry policy and the fetch verdict can't drift apart.
_RETRYABLE_HTTP_STATUSES = HTTP_BAN_STATUSES | HTTP_TRANSIENT_STATUSES


def looks_like_proxy_failure(status_code: int | None, error: str | None) -> bool:
    if status_code is not None and status_code in _RETRYABLE_HTTP_STATUSES:
        return True
    if not error:
        return False
    error = error.lower()
    needles = (
        "proxy",
        "tunnel",
        "timed out",
        "timeout",
        "econnreset",
        "econnrefused",
        "enotfound",
        "dns",
        "net::err",
        "ns_error_",  # Firefox (Camoufox) network-error codes
        "connection closed",
        "socket hang up",
        "tls",
        "handshake",
    )
    return any(needle in error for needle in needles)


async def run_scrape(
    runner: PlaywrightRunner,
    request_id: str,
    request: dict[str, Any],
    storage_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Returns the same envelope the old result_q message carried, WITHOUT the
    outer "job_id" key:
      {"ok": True, "result": {...}, "storage_state": ...}   on success
      {"ok": False, "error": str[, "traceback": str]}       on failure
        (traceback only for unexpected crashes, not user config errors)
    """
    start_time = time.perf_counter()

    proxy_type_raw = request.get("proxy_type")
    proxy_pool_id = request.get("proxy_pool_id")
    proxy_geo_data = request.get("proxy_geo")
    url = str(request.get("url"))

    proxy_geo: dict[str, str] | None = None
    if proxy_geo_data:
        proxy_geo = {
            "country_code": proxy_geo_data.get("country_code"),
            "region": proxy_geo_data.get("region"),
            "city": proxy_geo_data.get("city"),
        }
        proxy_geo = {k: v for k, v in proxy_geo.items() if v is not None}
        if not proxy_geo:
            proxy_geo = None

    log.info(
        "job received request_id=%s proxy_type=%s proxy_pool_id=%s url=%s",
        request_id,
        proxy_type_raw,
        proxy_pool_id,
        url,
    )

    try:
        session = await proxy_resolver.open_session(
            proxy_type=proxy_type_raw,
            proxy_pool_id=proxy_pool_id,
            proxy_geo=proxy_geo,
            max_retries=request.get("max_retries"),
            prem_proxy_options=request.get("prem_proxy_options"),
        )

        attempts = session.max_attempts()
        fetch_result: FetchResult | None = None
        retries_used = 0

        # raw_html / screenshot can be requested via the legacy
        # booleans OR by listing them in `formats` (union). Resolve
        # once so capture and output agree.
        effective_formats = resolve_formats(
            request.get("formats"),
            raw_html=request.get("raw_html", False),
            screenshot=request.get("screenshot", False),
        )
        want_raw_html = "raw_html" in effective_formats
        want_screenshot = "screenshot" in effective_formats

        for attempt in range(1, attempts + 1):
            proxy_cfg = session.current_proxy()
            log.debug(
                "attempt %d/%d for request_id=%s with proxy=%s",
                attempt,
                attempts,
                request_id,
                proxy_cfg.server if proxy_cfg else "none"
            )
            timeout_ms = request.get("timeout_ms") or settings.request_timeout_ms
            log.debug(
                "fetching with timeout_ms=%d, wait_until=%s",
                timeout_ms,
                request.get("wait_until", "domcontentloaded")
            )

            fetch_result = await runner.fetch(
                url=request["url"],
                device=request.get("device", "desktop"),
                proxy=proxy_cfg,
                headers=request.get("headers"),
                wait_until=request.get("wait_until", "domcontentloaded"),
                wait_for_selector=request.get("wait_for_selector"),
                timeout_ms=request.get("timeout_ms"),
                screenshot=want_screenshot,
                element_selector=request.get("element_selector"),
                stealth=request.get("stealth", True),
                block_assets=request.get("block_assets"),
                proxy_geo=proxy_geo,
                render=request.get("render", True),
                cookies=request.get("cookies"),
                storage_state=storage_state,
                # Camoufox premium options; accepted-and-ignored by PlaywrightRunner
                humanize=request.get("humanize", False),
                spoof_os=request.get("spoof_os"),
                block_webgl=request.get("block_webgl", False),
                addons=request.get("addons"),
                warmup=request.get("warmup"),
            )

            # If it is success - exit from cycle
            if fetch_result.ok:
                log.info(
                    "fetch succeeded on attempt %d for request_id=%s",
                    attempt,
                    request_id
                )
                break

            # If is not success - check, should rotate proxy
            log.warning(
                "fetch failed on attempt %d for request_id=%s: %s",
                attempt,
                request_id,
                fetch_result.error
            )

            # Rotate + retry on a proxy/network failure OR a captcha/block: a
            # block means this IP is burned, so a fresh proxy is the fix (bounded
            # by max_attempts). Anything else is a genuine target response — don't
            # waste proxies retrying it.
            if not (fetch_result.blocked or looks_like_proxy_failure(
                fetch_result.status_code, fetch_result.error
            )):
                log.info(
                    "error is neither proxy failure nor block, stopping retries for request_id=%s",
                    request_id
                )
                break

            # Increment retry counter
            retries_used = attempt

            # Call on_failure for proxy rotation
            should_retry = await session.on_failure(
                ProxyFailure(
                    status_code=fetch_result.status_code,
                    error=fetch_result.error
                )
            )

            if not should_retry:
                log.warning(
                    "session.on_failure returned False, stopping retries for request_id=%s",
                    request_id
                )
                break

            log.info(
                "rotating to next proxy for request_id=%s, attempt %d/%d",
                request_id,
                attempt + 1,
                attempts
            )

        # --- Render result ---
        warnings: list[str] = []
        data = None

        if fetch_result is None:
            warnings.append("fetch_result_is_none")
            fetch_result = FetchResult(
                html="",
                final_url=None,
                status_code=None,
                screenshot_b64=None,
                ok=False,
                error="No fetch result"
            )

        if not fetch_result.ok:
            if fetch_result.error:
                warnings.append(fetch_result.error)
            if fetch_result.status_code:
                warnings.append(f"status_code={fetch_result.status_code}")

        # Parse data: preset pipeline (self-heal / AI) when a
        # parser_plan is present, else the plain deterministic
        # extract for raw /scrape callers.
        if (request.get("extract") or request.get("parser_plan")) and fetch_result.html:
            parsed_data, parse_warnings = await apply_parser(
                fetch_result.html,
                request.get("extract"),
                request.get("parser_plan"),
            )
            data = parsed_data
            warnings.extend(parse_warnings)

        raw_html = fetch_result.html if want_raw_html else None
        screenshot_b64 = fetch_result.screenshot_b64 if want_screenshot else None

        # Markdown-family outputs (markdown / fit_markdown / html /
        # links). Best-effort: failures degrade to warnings, never
        # fail the scrape. No-op for legacy callers.
        markdown_outputs, md_warnings = await apply_markdown(
            request,
            fetch_result.html,
            base_url=fetch_result.final_url or url,
        )
        warnings.extend(md_warnings)

        took_ms = int((time.perf_counter() - start_time) * 1000)

        # Echo the resolved premium targeting (suffix only — no credentials) so
        # clients can see which country/session/filter actually hit the gateway.
        # Set only by the prem provider; None for every other proxy type.
        final_proxy = session.current_proxy()
        prem_targeting = final_proxy.targeting_suffix if final_proxy else None

        # Send result
        return {
            "ok": True,
            "result": {
                "request_id": request_id,
                "took_ms": took_ms,
                "meta": {
                    "url": url,
                    "final_url": fetch_result.final_url,
                    "status_code": fetch_result.status_code,
                    "device": request.get("device", "desktop"),
                    "proxy_type": proxy_type_raw,
                    "proxy_pool_id": proxy_pool_id,
                    "retries": retries_used,
                    "fetch_ok": fetch_result.ok,
                    "applied_user_agent": fetch_result.applied_user_agent,
                    "applied_locale": fetch_result.applied_locale,
                    "applied_timezone": fetch_result.applied_timezone,
                    "applied_accept_language": fetch_result.applied_accept_language,
                    "applied_preset": request.get("preset_meta"),
                    "applied_prem_targeting": prem_targeting,
                    "applied_warmup": fetch_result.applied_warmup,
                },
                "data": data,
                "raw_html": raw_html,
                "markdown": markdown_outputs.get("markdown"),
                "fit_markdown": markdown_outputs.get("fit_markdown"),
                "markdown_references": markdown_outputs.get("markdown_references"),
                "links": markdown_outputs.get("links"),
                "html": markdown_outputs.get("html"),
                "screenshot_base64": screenshot_b64,
                "element_screenshot_status": fetch_result.element_status,
                "warnings": warnings,
            },
            "storage_state": fetch_result.storage_state,
        }

    except ProxyConfigError as e:
        # User input, not a code failure — WARNING without traceback.
        log.warning("proxy config error for request_id=%s: %s", request_id, e)
        return {
            "ok": False,
            # Same "TypeName: message" shape the session errors use in tasks.py.
            "error": f"{type(e).__name__}: {e}",
        }

    except Exception as e:
        error_traceback = traceback.format_exc(limit=20)
        log.error(
            "unexpected error in worker for request_id=%s: %s\n%s",
            request_id,
            str(e),
            error_traceback,
        )
        return {
            "ok": False,
            "error": str(e),
            "traceback": error_traceback,
        }
