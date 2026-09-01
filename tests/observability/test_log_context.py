"""Correlation that is injected onto every record, and cannot itself break logging.

The census that motivated this: 147 first-party log calls, 122 printf-style,
ZERO uses of `extra=`, and `job_id`/`request_id` living in two disjoint id
spaces that co-occur in exactly one line. The fix is a contextvar seeded once
per task and a record factory that stamps it, so no call site has to remember.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from src.observability.log_context import (
    bind_log_context,
    current_log_context,
    install_record_factory,
)


@pytest.fixture(autouse=True)
def _factory():
    install_record_factory()
    yield
    logging.setLogRecordFactory(logging.LogRecord)


def _record(msg: str = "x") -> logging.LogRecord:
    return logging.getLogger("probe").makeRecord(
        "probe", logging.INFO, "f", 1, msg, (), None
    )


def test_a_record_carries_the_bound_context():
    with bind_log_context(job_id="req_abc", page_index=3):
        rec = _record()
    assert rec.job_id == "req_abc"
    assert rec.page_index == 3


def test_a_record_outside_any_binding_still_builds():
    """Every one of the 147 call sites runs outside a binding sometimes —
    import time, startup, the scheduler. A missing key must not be an error."""
    rec = _record()
    assert getattr(rec, "job_id", None) is None


def test_the_binding_does_not_outlive_its_block():
    with bind_log_context(job_id="req_abc"):
        pass
    assert current_log_context() == {}
    assert getattr(_record(), "job_id", None) is None


def test_installing_twice_does_not_stack():
    """`setup_logging` runs per process and tests call it repeatedly. A factory
    that wraps itself would grow a chain and, eventually, recurse."""
    install_record_factory()
    install_record_factory()
    with bind_log_context(job_id="req_abc"):
        rec = _record()
    assert rec.job_id == "req_abc"


def test_a_broken_context_cannot_break_the_log_call(monkeypatch):
    """`logging.raiseExceptions` guards `Handler.emit`, NOT `Logger.makeRecord`
    — measured. An exception raised inside the factory propagates out of
    `log.info()` into caller code, which would turn all 147 call sites into
    crash sites.

    Modelled by breaking the context accessor itself rather than by passing a
    hostile value: `setattr` never renders what it stores, so a value with an
    exploding `__repr__` does not exercise this guard at all — the first
    version of this test asserted the property and could not see it.
    """
    import src.observability.log_context as mod

    class _BrokenVar:
        def get(self):
            raise RuntimeError("contextvar in a bad state")

    # ContextVar is a C type whose `get` is read-only, so the whole var is
    # swapped rather than its method patched.
    monkeypatch.setattr(mod, "_context", _BrokenVar())

    rec = _record()  # must not raise
    assert rec is not None
    assert rec.getMessage() == "x"


@pytest.mark.asyncio
async def test_sibling_tasks_do_not_see_each_others_context():
    """The worker runs one task per process, but a leak here would attribute
    one job's lines to the next — correlation that LIES is worse than none."""
    seen = {}

    async def _job(name: str) -> None:
        with bind_log_context(job_id=name):
            await asyncio.sleep(0)
            seen[name] = getattr(_record(), "job_id", None)

    await asyncio.gather(_job("req_a"), _job("req_b"))
    assert seen == {"req_a": "req_a", "req_b": "req_b"}


@pytest.mark.asyncio
async def test_a_binding_does_not_escape_into_the_caller():
    """Each asyncio task inherits a COPY of the parent context, so a task that
    binds cannot pollute the loop it was spawned from — which is the only
    reason the worker is safe today."""
    async def _job() -> None:
        with bind_log_context(job_id="req_inner"):
            await asyncio.sleep(0)

    await asyncio.create_task(_job())
    assert getattr(_record(), "job_id", None) is None


def test_nested_bindings_merge_and_unwind():
    with bind_log_context(job_id="req_abc"):
        with bind_log_context(page_index=2):
            rec = _record()
            assert (rec.job_id, rec.page_index) == ("req_abc", 2)
        assert getattr(_record(), "page_index", None) is None
        assert _record().job_id == "req_abc"
