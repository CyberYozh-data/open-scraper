import types, pytest
from src.queue.tasks import _get_runner
from src.browser.camoufox_runner import CamoufoxRunner


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
