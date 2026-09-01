"""Correlation carried on the record, not written at the call site.

The census that motivated this: 147 first-party log calls, 122 printf-style,
ZERO uses of `extra=`, and `job_id` / `request_id` living in two disjoint id
spaces that co-occur in exactly one line in the whole tree. Asking 147 call
sites to remember an id is how you get 146 that do and one that matters.

So the id lives in a `ContextVar` and a `logging.setLogRecordFactory` stamps it
onto every record. A call site that says nothing still gets correlated.

Two properties this module exists to guarantee, both measured rather than
assumed:

  * `logging.raiseExceptions` guards `Handler.emit`, NOT `Logger.makeRecord` —
    an exception raised inside a record factory propagates out of `log.info()`
    into caller code. A factory that can raise turns all 147 call sites into
    crash sites, so the body is total.
  * installing twice must not stack. `setup_logging` runs once per process but
    tests call it repeatedly, and a factory that wraps whatever it found would
    grow a chain and eventually recurse.

Task isolation comes free from asyncio: each task inherits a COPY of its
parent's context, so a binding inside a task cannot escape into the loop, and
siblings cannot see each other. That is the only reason the worker is safe
today — and the reason a correlation id must never be bound OUTSIDE a task
(e.g. in a worker-startup hook), where every later task would inherit it.
"""
from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

# The fields a record may gain from the ambient context. An allowlist, not a
# free-form dict: everything here can reach a log line, and this module must
# never become a side channel for values the redaction rules exclude.
CONTEXT_FIELDS = ("job_id", "request_id", "page_index", "engine")

_context: ContextVar[Mapping[str, Any]] = ContextVar("log_context", default={})

_INSTALLED_MARKER = "_open_scraper_log_factory"


def current_log_context() -> Mapping[str, Any]:
    return _context.get()


@contextlib.contextmanager
def bind_log_context(**fields: Any) -> Iterator[None]:
    """Add fields for the duration of the block, then restore exactly.

    Nested binds merge; unwinding restores the previous mapping wholesale
    rather than deleting keys, so an inner bind cannot erase an outer one.
    """
    unknown = set(fields) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"not a declared log context field: {sorted(unknown)}")
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def install_record_factory() -> None:
    """Stamp the ambient context onto every record created from now on.

    Idempotent: a second call replaces nothing, so `setup_logging` can be
    called per process and per test without growing a chain.
    """
    existing = logging.getLogRecordFactory()
    if getattr(existing, _INSTALLED_MARKER, False):
        return

    def _factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = existing(*args, **kwargs)
        # Total by construction. A factory that raises does not produce a
        # logging error — it propagates into whichever line called log.info().
        try:
            for key, value in _context.get().items():
                setattr(record, key, value)
        except Exception:  # pylint: disable=broad-except
            pass
        return record

    setattr(_factory, _INSTALLED_MARKER, True)
    logging.setLogRecordFactory(_factory)
