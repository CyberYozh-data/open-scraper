"""Does a real Chromium page actually get a WebGL context?

Every other test around this flag asserts that a string reaches an argument
list. None of them could have caught the bug they exist for: nothing in this
repo changed, Chrome's policy did (M136 stopped falling back to software WebGL
on its own in headful mode). An argument-list assertion sails through that.

So this one launches the browser and reads the page back. It is marked e2e and
excluded from `make test` / the CI gate, because it needs a real browser and an
X display — run it after a Chrome bump, which is exactly when the policy that
broke this can move again:

    docker compose exec -T -e DISPLAY=:99 web-scraper \\
        python -m pytest tests/browser/test_webgl_context_e2e.py -m e2e

`-e DISPLAY=:99` is not optional. The entrypoint exports DISPLAY for the service
process, but `docker compose exec` starts a fresh environment without it, so
without that flag the test SKIPS — and a skip reads like a pass to anyone
scanning the output.

Deliberately narrow: it drives the launch, not the request path. It opens a
plain context rather than going through `_new_context`, so it says nothing about
viewport, UA, Client-Hints or stealth — only whether the browser this repo
launches still gives a page a WebGL context.
"""
import os

import pytest

from src.browser.runner import PlaywrightRunner
from src.settings import settings


PROBE = """() => {
  const one = document.createElement('canvas');
  const two = document.createElement('canvas');
  const gl = one.getContext('webgl') || one.getContext('experimental-webgl');
  if (!gl) return {webgl: false, webgl2: !!two.getContext('webgl2')};
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  return {
    webgl: true,
    webgl2: !!two.getContext('webgl2'),
    renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
  };
}"""


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_a_real_chromium_page_has_a_webgl_context():
    """Headful, because headful is the deployed mode and the only broken one.

    Headless Chrome still falls back to software WebGL by itself, so a headless
    run would pass with the flag removed — asserting nothing about the case that
    actually failed.
    """
    if not os.environ.get("DISPLAY"):
        pytest.skip("headful launch needs an X display (Xvfb); DISPLAY is unset")
    if not settings.software_webgl:
        pytest.skip("SOFTWARE_WEBGL=false deliberately gives up the context")

    runner = PlaywrightRunner(engine="chromium", headless=False, block_assets=False, timeout_ms=30000)
    await runner.start()
    try:
        context = await runner._browser.new_context()
        page = await context.new_page()
        await page.goto("about:blank")
        result = await page.evaluate(PROBE)
        await context.close()
    finally:
        await runner.stop()

    assert result["webgl"], (
        "no WebGL context on a headful Chromium — a page can tell instantly, since "
        "real desktop Chrome effectively always has one. If this fails after a Chrome "
        "bump, the software-WebGL policy moved again."
    )
    assert result["webgl2"], "WebGL 2 missing while WebGL 1 is present"
    assert result["renderer"], "no renderer string behind the context"
