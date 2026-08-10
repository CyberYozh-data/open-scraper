"""Checks that need Camoufox itself, not a fake of it.

Every defect this feature came from was found by launching a browser and
reading the page — a fake answering on both sides would have reported all of
them healthy. So the checks that can only fail against the real thing live
here, behind the `e2e` marker `make test` excludes.
"""
from __future__ import annotations

import pytest

from src.browser.fingerprint_profile import _WEBGL_BY_OS_AND_VENDOR


@pytest.mark.e2e
@pytest.mark.parametrize("key,pair", sorted(_WEBGL_BY_OS_AND_VENDOR.items()))
def test_every_webgl_pair_is_one_camoufox_can_serve(key, pair):
    """`webgl_config` is validated against a table inside Camoufox.

    A pair that is not in it — or one whose probability is zero for the OS being
    claimed — raises ValueError while building the launch options, i.e. in
    production on the first scrape that picks that profile, with a message
    naming browserforge internals rather than the profile. The map is
    hand-copied from that table, so this is what notices when a Camoufox upgrade
    rewrites it.

    Driven through `launch_options` rather than the internal `sample_webgl`
    because that is the call production makes: it also covers the OS-name
    translation (`windows` -> `win`) that our map's keys depend on.
    """
    from camoufox.utils import launch_options

    target_os, _gpu_vendor = key

    opts = launch_options(headless=True, geoip=False, os=target_os, webgl_config=pair)

    assert opts, f"{key} -> {pair} produced no launch options"


@pytest.mark.e2e
def test_an_invented_pair_still_raises():
    """The premise of the test above.

    If Camoufox ever stopped validating `webgl_config`, the map would quietly
    become decoration and the check above would pass for the wrong reason.
    """
    from camoufox.utils import launch_options

    with pytest.raises(ValueError):
        launch_options(
            headless=True, geoip=False, os="windows",
            webgl_config=("Acme Graphics", "Acme Turbo 9000"),
        )


@pytest.mark.e2e
def test_a_pair_from_the_wrong_os_is_refused():
    """Camoufox rejects a pair whose probability is zero for the claimed OS.

    Apple's renderer under a Windows claim is the shape of the contradiction
    this whole feature exists to stop, and Camoufox will not assemble it — so
    the map cannot accidentally pair one OS's GPU with another's.
    """
    from camoufox.utils import launch_options

    with pytest.raises(ValueError):
        launch_options(
            headless=True, geoip=False, os="windows",
            webgl_config=_WEBGL_BY_OS_AND_VENDOR[("macos", "apple")],
        )


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile,expected_platform,expected_vendor",
    [
        ("windows_on_host", "Win32", "Google Inc. (AMD)"),
        ("host", "Linux x86_64", "AMD"),
    ],
)
async def test_the_pinned_values_reach_the_page(profile, expected_platform, expected_vendor):
    """The only check that can tell a pinned launch from an un-pinned one.

    Everything upstream of here can agree with itself and still be wrong: the
    resolver returns a dict, `build_camoufox_options` copies it into another
    dict, and neither knows whether Camoufox honoured any of it. This reads the
    values back out of a real page.

    Headful, because Camoufox has no WebGL context at all under `headless=True`
    in this container — `getContext('webgl')` returns null on every OS — so a
    headless run would assert against None and pass for the wrong reason.

    The expectations encode THIS host (AMD, Linux, integrated Radeon). On other
    hardware the vendor differs, which is what HOST_GPU_VENDOR exists for; the
    test is marked e2e and is not part of `make test`.
    """
    import os as _os

    if not _os.environ.get("DISPLAY"):
        pytest.skip("headful launch needs an X display (Xvfb); DISPLAY is unset")

    from camoufox.async_api import AsyncCamoufox

    from src.browser.camoufox_runner import build_camoufox_options
    from src.browser.fingerprint_profile import host_facts, resolve_fingerprint

    host_os, gpu_vendor = host_facts()
    if (host_os, gpu_vendor) != ("linux", "amd"):
        pytest.skip(f"expectations are written for linux/amd, this host is {host_os}/{gpu_vendor}")

    resolved = resolve_fingerprint(profile)
    opts = build_camoufox_options(
        proxy=None, block_assets=False, webrtc_block=True, headless=False,
        fingerprint=resolved, viewport={"width": 1920, "height": 1080},
    )

    read_back = """() => {
      const gl = document.createElement('canvas').getContext('webgl');
      const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
      return {
        platform: navigator.platform,
        webglVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
        outer: [outerWidth, outerHeight],
        screen: [screen.width, screen.height],
      };
    }"""

    async with AsyncCamoufox(**opts) as browser:
        page = await browser.new_page()
        await page.goto("https://example.com/", wait_until="domcontentloaded", timeout=60000)
        seen = await page.evaluate(read_back)

    assert seen["platform"] == expected_platform
    assert seen["webglVendor"] == expected_vendor
    # The geometry the screen floor guarantees, re-checked here because it is
    # the same launch and a regression in either would look like the other.
    assert seen["outer"][0] <= seen["screen"][0]
    assert seen["outer"][1] <= seen["screen"][1]
