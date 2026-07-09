"""Tests for per-engine runner registry and per-engine idle shutdown.

TDD: these tests were written BEFORE the implementation changes so they
drove the design (RED phase verified by running before the fix).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.queue import worker as worker_mod

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Tests from the task brief
# ---------------------------------------------------------------------------

def test_new_runner_accepts_engine():
    r = worker_mod._new_runner("firefox")
    assert r._engine == "firefox"


def test_new_runner_defaults_chromium():
    r = worker_mod._new_runner()
    assert r._engine == "chromium"


def test_secondary_idle_setting_exists():
    from src.settings import settings
    assert isinstance(settings.browser_idle_shutdown_s_secondary, (int, float))


# ---------------------------------------------------------------------------
# Per-engine isolation: _record_page
# ---------------------------------------------------------------------------

async def test_record_page_per_engine_isolation():
    """_record_page(context, 'firefox') must increment only firefox counter;
    chromium counter must stay untouched."""
    from src.queue.tasks import _record_page

    state = SimpleNamespace(
        pages_since_launch={"chromium": 3},
        last_activity={"chromium": 0.0},
    )

    class _FakeCtx:
        pass

    ctx = _FakeCtx()
    ctx.state = state  # type: ignore[attr-defined]

    _record_page(ctx, "firefox")

    # chromium counter unchanged
    assert state.pages_since_launch["chromium"] == 3
    # firefox counter created and incremented
    assert state.pages_since_launch["firefox"] == 1


async def test_record_page_default_engine_is_chromium():
    """Calling _record_page(ctx) without an engine arg bumps chromium."""
    from src.queue.tasks import _record_page

    state = SimpleNamespace(
        pages_since_launch={"chromium": 0},
        last_activity={"chromium": 0.0},
    )

    class _FakeCtx:
        pass

    ctx = _FakeCtx()
    ctx.state = state  # type: ignore[attr-defined]

    _record_page(ctx)

    assert state.pages_since_launch["chromium"] == 1


# ---------------------------------------------------------------------------
# _lifecycle_tick: secondary engine closes while primary stays warm
# ---------------------------------------------------------------------------

class _FakeRunner:
    def __init__(self, started: bool = True) -> None:
        self._started = started
        self.stop_calls = 0

    def is_started(self) -> bool:
        return self._started

    async def stop(self) -> None:
        self.stop_calls += 1
        self._started = False


async def test_lifecycle_tick_closes_idle_secondary_leaves_primary(monkeypatch):
    """An idle secondary engine (firefox) must be closed by _lifecycle_tick
    while a recently-active primary (chromium) stays open."""
    monkeypatch.setattr(worker_mod.settings, "browser_idle_shutdown_s", 600.0)
    monkeypatch.setattr(worker_mod.settings, "browser_idle_shutdown_s_secondary", 120.0)
    monkeypatch.setattr(worker_mod.settings, "browser_max_pages", 0)

    now = asyncio.get_running_loop().time()
    chromium_runner = _FakeRunner(started=True)
    firefox_runner = _FakeRunner(started=True)

    state = SimpleNamespace(
        runners={"chromium": chromium_runner, "firefox": firefox_runner},
        browser_lock=asyncio.Lock(),
        pages_since_launch={"chromium": 0, "firefox": 0},
        last_activity={
            "chromium": now - 10,    # active 10s ago — well within 600s window
            "firefox": now - 200,    # idle 200s > 120s secondary window
        },
    )

    await worker_mod._lifecycle_tick(state)

    assert chromium_runner.stop_calls == 0, "primary (chromium) should NOT be stopped"
    assert firefox_runner.stop_calls == 1, "secondary (firefox) should be stopped"


async def test_lifecycle_tick_secondary_idle_window_not_primary(monkeypatch):
    """A secondary engine that has been idle for 150s (> 120s secondary window
    but < 600s primary window) must be closed even when primary is also idle
    by the same 150s — because the primary window is larger."""
    monkeypatch.setattr(worker_mod.settings, "browser_idle_shutdown_s", 600.0)
    monkeypatch.setattr(worker_mod.settings, "browser_idle_shutdown_s_secondary", 120.0)
    monkeypatch.setattr(worker_mod.settings, "browser_max_pages", 0)

    now = asyncio.get_running_loop().time()
    chromium_runner = _FakeRunner(started=True)
    webkit_runner = _FakeRunner(started=True)

    state = SimpleNamespace(
        runners={"chromium": chromium_runner, "webkit": webkit_runner},
        browser_lock=asyncio.Lock(),
        pages_since_launch={"chromium": 0, "webkit": 0},
        last_activity={
            "chromium": now - 150,   # 150s — within 600s primary window (not idle)
            "webkit": now - 150,     # 150s — exceeds 120s secondary window (idle)
        },
    )

    await worker_mod._lifecycle_tick(state)

    assert chromium_runner.stop_calls == 0, "primary (chromium) 150s < 600s, should stay open"
    assert webkit_runner.stop_calls == 1, "secondary (webkit) 150s > 120s, should close"
