"""Guards for ScrapeMeta fields that arrive across a version boundary.

The test is WHO PRODUCED THE VALUE, not whether the field looks diagnostic.
`device`, `proxy_type`, `proxy_pool_id` and `applied_preset` come off the stored
page dict — written by one deploy, read by another, with a 24h TTL in between —
so a value this worker does not recognise is expected skew, and no such echo is
worth the page. Degrade the field, keep the html/markdown/screenshot.

A value produced IN-PROCESS by our own runner is the opposite case and must NOT
be guarded: `element_screenshot_status` has no old-worker/new-API story, so an
unknown value there is two files in the same image disagreeing — and `runner.py`,
which produces it, is outside the mypy scope, so rejecting the response is the
only signal that drift exists at all.

They belong here rather than in `tasks` because `run_scrape` needs them too and
`tasks` already imports it.
"""
from __future__ import annotations

import logging
from typing import Any, TypeVar, get_args

from pydantic import BaseModel

from src.schemas import Device, ScrapeProxyType

log = logging.getLogger(__name__)

# One home for the sets the guards check against. They were duplicated in
# `tasks` and `scrape_runner`, and only the `tasks` copy had a test — so swapping
# the `scrape_runner` pair, which is on the success path of EVERY scrape, left
# the suite green while reporting `desktop`/`none` for every mobile or proxied
# request.
DEVICES: frozenset[Device] = frozenset(get_args(Device))
PROXY_TYPES: frozenset[ScrapeProxyType] = frozenset(get_args(ScrapeProxyType))

# Values are truncated in the logs: a page dict field is unbounded, and a
# rejected 2 MB value must not become a 2 MB log line.
_LOG_REPR_LIMIT = 200


def _tell(sink: list[str] | None, field: str, fallback: Any) -> None:
    """Say it to the caller too, not only to the worker log.

    Every other degradation in `run_scrape` — a missing fetch result, a markdown
    failure, a parse warning — appends to `ScrapeResponse.warnings`. A silently
    substituted `meta.proxy_type` is a confidently wrong answer with nothing to
    show for it, which is the failure this project keeps re-learning.

    The text is a cross-repo contract because it embeds the field NAME:
    yozh-law-checker substring-matches these warnings for "timeout"/"goto" (to
    re-crawl with a different wait strategy) and "captcha"/"block detected" (to
    publish a scan as blocked). No guarded field is named that today; the first
    one that is would fire a spurious re-crawl of a whole site.
    """
    if sink is not None:
        sink.append(f"meta field '{field}' degraded to {fallback!r}: unrecognised value")


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= _LOG_REPR_LIMIT else text[:_LOG_REPR_LIMIT] + "…"


L = TypeVar("L", bound=str)
M = TypeVar("M", bound=BaseModel)


def literal_or(
    value: Any, allowed: frozenset[L], fallback: L, *, field: str,
    sink: list[str] | None = None,
) -> L:
    """Keep `value` only if the schema's Literal admits it.

    The isinstance check is not redundant: `value in frozenset` raises
    TypeError on an unhashable value, which is the same failure as an unknown
    literal arriving through a different door.
    """
    if isinstance(value, str):
        # Returning the MEMBER rather than the argument is what keeps this
        # `Literal`-typed without a cast: `value` is `Any`, `candidate` is `L`.
        # Tying `allowed` to the same TypeVar also makes passing the wrong set a
        # mypy error, which is the mistake that shipped here untested. A narrow
        # `# type: ignore[return-value]` would have worked too, but under
        # `warn_unused_ignores` it is a two-sided trap: a mypy release that stops
        # emitting that exact code turns the ignore itself into the error.
        for candidate in allowed:
            if candidate == value:
                return candidate
    if value is not None:
        log.warning(
            "field_degraded field=%s fallback=%r rejected=%s",
            field, fallback, _short(value),
        )
        _tell(sink, field, fallback)
    return fallback


def str_or_none(value: Any, *, field: str, sink: list[str] | None = None) -> str | None:
    """Keep `value` only if the schema's `str | None` admits it."""
    if value is None or isinstance(value, str):
        return value
    log.warning("field_degraded field=%s fallback=None rejected=%s", field, _short(value))
    _tell(sink, field, None)
    return None


def model_or_none(
    model: type[M], value: Any, *, field: str, sink: list[str] | None = None,
) -> M | None:
    """Validate an optional nested model, degrading it rather than failing.

    `applied_preset` is rebuilt from a stored dict and `applied_warmup` from
    run_warmup's own; both are subject to the drift the guards above absorb.
    """
    if value is None:
        return None
    try:
        return model.model_validate(value)
    # Degrade the field, never drop the page — the prose has to live on its own
    # line: pylint reads everything after `disable=` as message names, so a
    # trailing explanation becomes five unknown-option-value warnings.
    except Exception as exc:  # pylint: disable=broad-except
        log.warning(
            "field_degraded field=%s fallback=None rejected=%s (%s)",
            field, _short(value), exc.__class__.__name__,
        )
        _tell(sink, field, None)
        return None
