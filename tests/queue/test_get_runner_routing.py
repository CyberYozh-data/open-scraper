import types, pytest
from src.queue.tasks import _get_runner
from src.browser.camoufox_runner import CamoufoxRunner
from src.browser.ephemeral_runner import EphemeralPlaywrightRunner
from src.settings import settings


class _State:
    def __init__(self):
        import asyncio
        self.runners = {}
        self.last_activity = {}
        self.pages_since_launch = {}
        self.browser_lock = asyncio.Lock()


def _ctx():
    return types.SimpleNamespace(state=_State())


@pytest.mark.asyncio
async def test_camoufox_returns_fresh_runner():
    r = await _get_runner(_ctx(), "camoufox")
    assert isinstance(r, CamoufoxRunner)


@pytest.mark.asyncio
async def test_native_engine_lazily_registered():
    ctx = _ctx()
    r = await _get_runner(ctx, "firefox")
    assert ctx.state.runners["firefox"] is r
    assert r._engine == "firefox"
    await r.stop()


@pytest.mark.asyncio
async def test_unset_headless_uses_warm_pool():
    """None = server default -> the existing warm, pooled runner."""
    ctx = _ctx()
    r = await _get_runner(ctx, "firefox")
    assert ctx.state.runners["firefox"] is r
    await r.stop()


@pytest.mark.asyncio
async def test_default_mode_uses_warm_pool():
    ctx = _ctx()
    r = await _get_runner(ctx, "firefox", settings.headless)
    assert ctx.state.runners["firefox"] is r
    await r.stop()


@pytest.mark.asyncio
async def test_non_default_mode_is_ephemeral_and_never_pooled():
    """The whole point: a second launch mode must not become a second warm browser."""
    ctx = _ctx()
    r = await _get_runner(ctx, "chromium", not settings.headless)
    assert isinstance(r, EphemeralPlaywrightRunner)
    assert ctx.state.runners == {}
    assert r.is_started() is False


@pytest.mark.asyncio
async def test_camoufox_honours_requested_launch_mode():
    r = await _get_runner(_ctx(), "camoufox", not settings.headless)
    assert isinstance(r, CamoufoxRunner)
    assert r._headless is (not settings.headless)
