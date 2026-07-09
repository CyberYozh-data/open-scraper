import pytest
from src.browser.runner import PlaywrightRunner

@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
@pytest.mark.asyncio
async def test_runner_launches_each_engine(engine):
    runner = PlaywrightRunner(engine=engine, headless=True, block_assets=False, timeout_ms=30000)
    await runner.start()
    try:
        assert runner.is_started()
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_launch_kwargs_per_engine(monkeypatch):
    """No engine gets a launch-level proxy placeholder.

    Firefox/WebKit honour a per-context proxy without a launch placeholder, and
    the old {"server": "per-context"} placeholder broke no-proxy contexts
    (NS_ERROR_UNKNOWN_PROXY_HOST). Also verifies that Chromium-only flags
    (AutomationControlled) are absent from the Firefox launch kwargs.
    """
    captured: dict[str, dict] = {}

    class _FakeBrowserType:
        def __init__(self, name):
            self.name = name

        async def launch(self, **kwargs):
            captured[self.name] = dict(kwargs)
            # Return a minimal stub; start() checks _browser is not None.
            return object()

    class _FakePlaywright:
        chromium = _FakeBrowserType("chromium")
        firefox = _FakeBrowserType("firefox")
        webkit = _FakeBrowserType("webkit")

        async def stop(self):
            pass

    class _FakePlaywrightCM:
        async def __aenter__(self):
            return _FakePlaywright()

        async def __aexit__(self, *_):
            pass

        async def start(self):
            return _FakePlaywright()

    import src.browser.runner as runner_mod

    # Patch async_playwright so start() uses our fake.
    monkeypatch.setattr(runner_mod, "async_playwright", lambda: _FakePlaywrightCM())

    for engine in ("chromium", "firefox", "webkit"):
        runner = PlaywrightRunner(engine=engine, headless=True, block_assets=False, timeout_ms=30000)
        # Bypass the _lock guard and directly call the start path via start().
        # Reset _browser each iteration so start() runs the launch branch.
        runner._browser = None
        await runner.start()

    # Chromium: no proxy placeholder, automation-controlled flag present.
    chromium_kw = captured["chromium"]
    assert "proxy" not in chromium_kw, "Chromium must NOT receive a per-context proxy placeholder"
    assert any(
        "AutomationControlled" in arg for arg in chromium_kw.get("args", [])
    ), "Chromium must carry --disable-blink-features=AutomationControlled"

    # Firefox: no proxy placeholder, no Chromium-only flags.
    firefox_kw = captured["firefox"]
    assert "proxy" not in firefox_kw, "Firefox must NOT receive a launch-level proxy placeholder"
    assert not any(
        "AutomationControlled" in arg for arg in firefox_kw.get("args", [])
    ), "Firefox must NOT carry Chromium-only --disable-blink-features=AutomationControlled"

    # WebKit: no proxy placeholder.
    webkit_kw = captured["webkit"]
    assert "proxy" not in webkit_kw, "WebKit must NOT receive a launch-level proxy placeholder"


def test_default_engine_is_chromium():
    runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
    assert runner._engine == "chromium"


# No engine launches under a proxy placeholder, so a no-proxy context passes
# proxy=None on every engine and Playwright connects directly. (Regression: the
# old Firefox/WebKit per-context placeholder leaked into no-proxy contexts as a
# literal host and broke navigation with NS_ERROR_UNKNOWN_PROXY_HOST.)
@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
@pytest.mark.asyncio
async def test_no_proxy_context_is_direct(engine):
    captured: dict = {}

    class _FakeContext:
        async def add_init_script(self, *_args, **_kwargs):
            pass

        async def route(self, *_args, **_kwargs):
            pass

    class _FakeBrowser:
        async def new_context(self, **kwargs):
            captured.update(kwargs)
            return _FakeContext()

    runner = PlaywrightRunner(engine=engine, headless=True, block_assets=False, timeout_ms=30000)
    runner._browser = _FakeBrowser()

    await runner._new_context(device="desktop", proxy=None, headers=None)

    assert captured["proxy"] is None


# The other half of the regression: a real per-context proxy must reach
# new_context on every engine (this is what makes the launch placeholder
# unnecessary — Firefox/WebKit honour the context-level proxy directly).
@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
@pytest.mark.asyncio
async def test_per_context_proxy_reaches_new_context(engine):
    from src.proxy.models import ProxyConfig

    captured: dict = {}

    class _FakeContext:
        async def add_init_script(self, *_args, **_kwargs):
            pass

        async def route(self, *_args, **_kwargs):
            pass

    class _FakeBrowser:
        async def new_context(self, **kwargs):
            captured.update(kwargs)
            return _FakeContext()

    runner = PlaywrightRunner(engine=engine, headless=True, block_assets=False, timeout_ms=30000)
    runner._browser = _FakeBrowser()

    proxy = ProxyConfig(server="http://1.2.3.4:8080", username="u", password="p")
    await runner._new_context(device="desktop", proxy=proxy, headers=None)

    assert captured["proxy"] == {"server": "http://1.2.3.4:8080", "username": "u", "password": "p"}
