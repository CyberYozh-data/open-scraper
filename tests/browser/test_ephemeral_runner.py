import asyncio

import pytest

from src.browser.ephemeral_runner import EphemeralPlaywrightRunner


class _FakeInner:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1

    async def fetch(self, *args, **kwargs):
        return "RESULT"


def _runner():
    return EphemeralPlaywrightRunner(
        engine="chromium", headless=False, block_assets=True, timeout_ms=1000
    )


def test_never_reports_warm():
    """The lifecycle loop skips runners that aren't started; this one never is."""
    assert _runner().is_started() is False


@pytest.mark.asyncio
async def test_start_and_stop_are_noops():
    r = _runner()
    assert await r.start() is None
    assert await r.stop() is None


@pytest.mark.asyncio
async def test_fetch_launches_then_closes():
    r = _runner()
    fake = _FakeInner()
    r._inner = fake
    assert await r.fetch("https://example.com") == "RESULT"
    assert (fake.started, fake.stopped) == (1, 1)


@pytest.mark.asyncio
async def test_fetch_closes_even_when_fetch_raises():
    """A throwaway browser must not leak when the fetch blows up."""
    r = _runner()
    fake = _FakeInner()

    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    fake.fetch = _boom
    r._inner = fake
    with pytest.raises(RuntimeError, match="boom"):
        await r.fetch("https://example.com")
    assert fake.stopped == 1


@pytest.mark.asyncio
async def test_fetch_closes_even_when_start_raises():
    """A throwaway browser must not leak when start() fails."""
    r = _runner()
    fake = _FakeInner()

    async def _start_fails():
        raise RuntimeError("start_boom")

    fake.start = _start_fails
    r._inner = fake
    with pytest.raises(RuntimeError, match="start_boom"):
        await r.fetch("https://example.com")
    assert fake.stopped == 1


@pytest.mark.asyncio
async def test_fetch_closes_the_browser_when_cancelled_mid_fetch():
    """page_task_timeout_s drives asyncio.wait_for, which cancels mid-fetch. An
    ephemeral browser is not in state.runners, so no lifecycle path can reclaim
    it — if teardown is skipped on cancellation the leak is permanent."""
    r = _runner()
    fake = _FakeInner()
    started = asyncio.Event()

    async def _hang(*args, **kwargs):
        started.set()
        await asyncio.sleep(9999)

    fake.fetch = _hang
    r._inner = fake

    task = asyncio.create_task(r.fetch("https://example.com"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.stopped == 1


@pytest.mark.asyncio
async def test_teardown_failure_does_not_mask_the_fetch_error():
    """The worker reports the fetch's exception; a wedged close() must not
    replace 'net::ERR_...' with an unrelated teardown error at that boundary."""
    r = _runner()
    fake = _FakeInner()

    async def _fetch_boom(*args, **kwargs):
        raise RuntimeError("original_fetch_error")

    async def _stop_boom():
        raise RuntimeError("teardown_error")

    fake.fetch = _fetch_boom
    fake.stop = _stop_boom
    r._inner = fake
    with pytest.raises(RuntimeError, match="original_fetch_error"):
        await r.fetch("https://example.com")


@pytest.mark.asyncio
async def test_teardown_failure_on_a_successful_fetch_does_not_raise():
    """A best-effort close must not turn a good result into a failed scrape."""
    r = _runner()
    fake = _FakeInner()

    async def _stop_boom():
        raise RuntimeError("teardown_error")

    fake.stop = _stop_boom
    r._inner = fake
    assert await r.fetch("https://example.com") == "RESULT"
