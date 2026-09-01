from __future__ import annotations

import base64
import logging
import re
import time
from typing import Any

from src.security.egress import (
    EGRESS_BLOCKED_ERROR,
    EgressBlocked,
    assert_landing_public,
    assert_navigable,
)
from src.sessions.models import LoginScript, LoginStep, SessionLoginResult


log = logging.getLogger(__name__)

_CRED_PATTERN = re.compile(r"\$creds_([a-zA-Z0-9_]+)")


def _substitute_creds(value: str, creds: dict[str, str]) -> str:
    """Replace $creds_<key> tokens with values from `creds`. Unknown tokens stay literal."""
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return creds.get(key, match.group(0))
    return _CRED_PATTERN.sub(replace, value)


def _redact_creds(text: str, creds: dict[str, str]) -> str:
    """Strip any credential value out of an error message before it leaves the worker.

    Playwright errors occasionally echo the value passed to `fill`/`type` back in
    their message; once substitution has run that value is the real password. We
    pass the result envelope back to the API client, so the redaction has to
    happen before we serialize.
    """
    for cred_value in creds.values():
        if cred_value:
            text = text.replace(cred_value, "***")
    return text


class LoginRunner:
    """Walks a LoginScript step-by-step against a Playwright Page.

    Pure side-effect engine — the caller is responsible for creating the page,
    binding the proxy/device profile, and closing the page when done. The
    runner doesn't own the browser lifecycle.
    """

    async def replay(
        self,
        *,
        page: Any,                          # playwright.async_api.Page
        context: Any,                       # playwright.async_api.BrowserContext
        script: LoginScript,
        creds: dict[str, str],
        resolve_dns: bool = True,
    ) -> tuple[SessionLoginResult, dict[str, Any] | None]:
        start = time.perf_counter()
        try:
            await self._execute_steps(page, script, creds, resolve_dns=resolve_dns)
        except EgressBlocked as exc:
            # No screenshot. The page is sitting on the address we just
            # refused, so `_try_screenshot` would hand the caller a rendered
            # image of exactly the content the refusal exists to withhold —
            # measured at 2,067,659 pixels of an internal admin page. The
            # error is the constant for the same reason: it names no host.
            failed_index = getattr(exc, "_login_step_index", None)
            log.warning("login_runner step refused by egress policy: index=%s", failed_index)
            return (
                SessionLoginResult(
                    ok=False,
                    failed_step_index=failed_index,
                    failed_step=getattr(exc, "_login_step", None),
                    error=EGRESS_BLOCKED_ERROR,
                    screenshot_b64=None,
                    took_ms=int((time.perf_counter() - start) * 1000),
                ),
                None,
            )
        except Exception as exc:  # pylint: disable=broad-except
            failed_index = getattr(exc, "_login_step_index", None)
            failed_step = getattr(exc, "_login_step", None)
            failed_op = failed_step.op if failed_step is not None else None
            log.warning(
                "login_runner step failed: index=%s op=%s exc=%s",
                failed_index,
                failed_op,
                type(exc).__name__,
            )
            screenshot_b64 = await self._try_screenshot(page)
            return (
                SessionLoginResult(
                    ok=False,
                    failed_step_index=failed_index,
                    failed_step=failed_step,
                    error=_redact_creds(str(exc), creds),
                    screenshot_b64=screenshot_b64,
                    took_ms=int((time.perf_counter() - start) * 1000),
                ),
                None,
            )

        if script.success_selector:
            try:
                await page.wait_for_selector(script.success_selector, timeout=10_000)
            except Exception as exc:  # pylint: disable=broad-except
                log.warning(
                    "login_runner success_selector check failed: exc=%s",
                    type(exc).__name__,
                )
                screenshot_b64 = await self._try_screenshot(page)
                return (
                    SessionLoginResult(
                        ok=False,
                        failed_step_index=None,
                        error=_redact_creds(f"success_selector not found: {exc}", creds),
                        screenshot_b64=screenshot_b64,
                        took_ms=int((time.perf_counter() - start) * 1000),
                    ),
                    None,
                )

        if script.success_url_regex:
            current_url = getattr(page, "url", "")
            try:
                matched = re.search(script.success_url_regex, current_url) is not None
            except re.error as exc:
                log.warning(
                    "login_runner success_url_regex invalid: exc=%s",
                    type(exc).__name__,
                )
                screenshot_b64 = await self._try_screenshot(page)
                return (
                    SessionLoginResult(
                        ok=False,
                        failed_step_index=None,
                        error=f"success_url_regex is invalid: {exc}",
                        screenshot_b64=screenshot_b64,
                        took_ms=int((time.perf_counter() - start) * 1000),
                    ),
                    None,
                )
            if not matched:
                log.warning("login_runner success_url_regex did not match")
                screenshot_b64 = await self._try_screenshot(page)
                return (
                    SessionLoginResult(
                        ok=False,
                        failed_step_index=None,
                        error=f"success_url_regex did not match (url={current_url})",
                        screenshot_b64=screenshot_b64,
                        took_ms=int((time.perf_counter() - start) * 1000),
                    ),
                    None,
                )

        storage_state = await context.storage_state()
        return (
            SessionLoginResult(
                ok=True,
                took_ms=int((time.perf_counter() - start) * 1000),
            ),
            storage_state,
        )

    async def _execute_steps(
        self,
        page: Any,
        script: LoginScript,
        creds: dict[str, str],
        *,
        resolve_dns: bool = True,
    ) -> None:
        for index, step in enumerate(script.steps):
            try:
                await self._dispatch(page, step, creds, resolve=resolve_dns)
            except Exception as exc:
                exc._login_step_index = index  # type: ignore[attr-defined]
                exc._login_step = step  # type: ignore[attr-defined]
                raise

    async def _dispatch(
        self, page: Any, step: LoginStep, creds: dict[str, str], *, resolve: bool = True
    ) -> None:
        op = step.op
        value = _substitute_creds(step.value, creds) if step.value is not None else None
        url = _substitute_creds(step.url, creds) if step.url is not None else None

        if op == "goto":
            if not url:
                raise ValueError("goto requires url")
            # The full address check, not just the scheme. The context route
            # guard this page inherits is blind to redirect hops, and a failing
            # login step returns a SCREENSHOT of whatever it landed on
            # (`_try_screenshot` below) — a full-read primitive, so a scheme
            # check alone was not enough. Re-checked here rather than only on
            # the model because `$creds_` substitution happens after validation
            # and a credential value can carry its own scheme and host.
            await assert_navigable(url, resolve=resolve)
            response = await page.goto(url)
            await assert_landing_public(response, page.url, resolve=resolve)
        elif op == "fill":
            if not step.selector or value is None:
                raise ValueError("fill requires selector and value")
            await page.fill(step.selector, value)
        elif op == "click":
            if not step.selector:
                raise ValueError("click requires selector")
            await page.click(step.selector)
        elif op == "wait_for_selector":
            if not step.selector:
                raise ValueError("wait_for_selector requires selector")
            await page.wait_for_selector(
                step.selector, timeout=step.timeout_ms or 30_000,
            )
        elif op == "wait_for_timeout":
            if step.ms is None:
                raise ValueError("wait_for_timeout requires ms")
            await page.wait_for_timeout(step.ms)
        elif op == "press_key":
            if not step.selector or not step.key:
                raise ValueError("press_key requires selector and key")
            await page.press(step.selector, step.key)
        elif op == "type_text":
            if not step.selector or value is None:
                raise ValueError("type_text requires selector and value")
            await page.type(step.selector, value)
        elif op == "hover":
            if not step.selector:
                raise ValueError("hover requires selector")
            await page.hover(step.selector)
        else:  # pragma: no cover — Pydantic Literal already rejects others
            raise ValueError(f"unsupported op: {op}")

    @staticmethod
    async def _try_screenshot(page: Any) -> str | None:
        try:
            png = await page.screenshot(full_page=True)
            return base64.b64encode(png).decode("ascii")
        except Exception:  # pylint: disable=broad-except
            return None
