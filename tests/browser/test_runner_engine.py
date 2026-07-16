import pytest
from src.browser.runner import (
    PlaywrightRunner,
    _align_ua_to_engine,
    _navigator_platform_for_ua,
)


def test_navigator_platform_for_ua_covers_os_families():
    win = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/143.0.0.0"
    mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/143.0.0.0"
    android = "Mozilla/5.0 (Linux; Android 14) Chrome/143.0.0.0 Mobile"
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) Version/17.4"
    linux = "Mozilla/5.0 (X11; Linux x86_64) Chrome/143.0.0.0"
    assert _navigator_platform_for_ua(win) == "Win32"
    assert _navigator_platform_for_ua(mac) == "MacIntel"
    assert _navigator_platform_for_ua(android) == "Linux armv8l"
    assert _navigator_platform_for_ua(iphone) == "iPhone"
    assert _navigator_platform_for_ua(linux) == "Linux x86_64"


def test_align_ua_to_engine_rewrites_chrome_major():
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    out = _align_ua_to_engine(ua, "143.0.7499.4")
    assert "Chrome/143.0.0.0" in out
    assert "Chrome/124" not in out
    # everything around the version token is preserved
    assert out.startswith("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    assert out.endswith("Safari/537.36")


def test_align_ua_to_engine_ignores_non_chrome_ua():
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) Version/17.4 Mobile/15E148 Safari/604.1"
    assert _align_ua_to_engine(ua, "143.0.7499.4") == ua


def test_align_ua_to_engine_ignores_unparseable_version():
    ua = "...Chrome/124.0.0.0 Safari/537.36"
    assert _align_ua_to_engine(ua, "unknown") == ua


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


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,engine,expect_channel", [
    ("chrome", "chromium", "chrome"),   # configured + chromium -> passed through
    (None, "chromium", None),           # unset -> bundled Chromium, no channel
    ("chrome", "firefox", None),        # channel is Chromium-only, never on firefox
])
async def test_chrome_channel_launch_kwarg(monkeypatch, channel, engine, expect_channel):
    import src.browser.runner as runner_mod
    monkeypatch.setattr(runner_mod.settings, "chrome_channel", channel)

    captured = {}

    class _BT:
        def __init__(self, name):
            self.name = name

        async def launch(self, **kwargs):
            captured.update(kwargs)
            return object()

    class _PW:
        chromium = _BT("chromium")
        firefox = _BT("firefox")
        webkit = _BT("webkit")

        async def stop(self):
            pass

    class _CM:
        async def __aenter__(self):
            return _PW()

        async def __aexit__(self, *_):
            pass

        async def start(self):
            return _PW()

    monkeypatch.setattr(runner_mod, "async_playwright", lambda: _CM())
    runner = PlaywrightRunner(engine=engine, headless=True, block_assets=False, timeout_ms=30000)
    runner._browser = None
    await runner.start()

    assert captured.get("channel") == expect_channel


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
        version = "143.0.7499.4"

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
        version = "143.0.7499.4"

        async def new_context(self, **kwargs):
            captured.update(kwargs)
            return _FakeContext()

    runner = PlaywrightRunner(engine=engine, headless=True, block_assets=False, timeout_ms=30000)
    runner._browser = _FakeBrowser()

    proxy = ProxyConfig(server="http://1.2.3.4:8080", username="u", password="p")
    await runner._new_context(device="desktop", proxy=proxy, headers=None)

    assert captured["proxy"] == {"server": "http://1.2.3.4:8080", "username": "u", "password": "p"}


class _CapturingBrowser:
    version = "143.0.7499.4"

    def __init__(self, captured: dict):
        self._captured = captured

    async def new_context(self, **kwargs):
        self._captured.update(kwargs)

        class _Ctx:
            async def add_init_script(self, *_a, **_k):
                pass

            async def route(self, *_a, **_k):
                pass

        return _Ctx()


@pytest.mark.asyncio
async def test_default_desktop_viewport_is_1920x1080_and_screen_matches():
    """Desktop default is 1920x1080 and window.screen is set to the same size.

    A window larger than the reported screen is a fingerprint tell, so screen
    must always equal the viewport.
    """
    captured: dict = {}
    runner = PlaywrightRunner(engine="chromium", headless=True, block_assets=False, timeout_ms=30000)
    runner._browser = _CapturingBrowser(captured)

    await runner._new_context(device="desktop", proxy=None, headers=None)

    assert captured["viewport"] == {"width": 1920, "height": 1080}
    assert captured["screen"] == captured["viewport"]


@pytest.mark.asyncio
async def test_custom_viewport_overrides_preset_on_both_viewport_and_screen():
    captured: dict = {}
    runner = PlaywrightRunner(engine="chromium", headless=True, block_assets=False, timeout_ms=30000)
    runner._browser = _CapturingBrowser(captured)

    vp = {"width": 1366, "height": 768}
    await runner._new_context(device="desktop", proxy=None, headers=None, viewport=vp)

    assert captured["viewport"] == vp
    assert captured["screen"] == vp
