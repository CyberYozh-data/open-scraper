"""What GPU Chromium claims, and where that claim is allowed to come from.

Everything here asserts on what `chromium_webgl_identity()` RETURNS and on what
reaches the Stealth object, never on the contents of the table. A guard that
reads the table cannot see a function that decorates its result, reads the wrong
table, or is not wired to the browser at all — all three of which survived an
earlier version of this file.
"""
import pytest

from src.browser import fingerprint_profile as fp
from src.browser import runner as runner_mod


AMD = ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon(TM) 780M (0x000015BF) Direct3D11 vs_5_0 ps_5_0, D3D11)")
INTEL = ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)")


@pytest.fixture
def host_vendor(monkeypatch):
    """Pin the detected host vendor, so a test says the same thing on any CI runner."""
    def _set(vendor):
        monkeypatch.setattr(fp, "host_facts", lambda: ("linux", vendor))
        return fp.chromium_webgl_identity()
    return _set


@pytest.mark.parametrize("vendor,expected", [("amd", AMD), ("intel", INTEL)])
def test_the_claim_follows_the_host_vendor(host_vendor, vendor, expected):
    """An AMD box must not claim an Intel card.

    Pinned to literal strings rather than to whatever the table returns: with the
    expectation derived from the same source as the answer, both move together
    and the test passes no matter what the code does.
    """
    assert host_vendor(vendor) == expected


@pytest.mark.parametrize("vendor", [None, "nvidia", "apple"])
def test_an_unmapped_vendor_blends_instead_of_guessing(host_vendor, vendor, caplog):
    """No row, no invention: fall back to the most common desktop GPU class.

    An empty override is not an option — playwright_stealth reads a falsy value
    as "use my default", which is a macOS pair under a Windows UA, i.e. the
    contradiction this override exists to remove.

    The warning is part of the contract: on such a host Camoufox still claims
    that vendor from its own table while Chromium claims Intel, so the engines
    diverge again and an operator has to be able to see it.
    """
    with caplog.at_level("WARNING"):
        assert host_vendor(vendor) == INTEL

    warnings = [r.getMessage() for r in caplog.records if "chromium WebGL" in r.getMessage()]
    assert warnings, caplog.text
    # The vendor has to be IN the line: an operator reading "no Chrome-shaped
    # string" without knowing which vendor was detected cannot tell whether
    # HOST_GPU_VENDOR took effect at all.
    assert (vendor or "unknown") in warnings[0], warnings


@pytest.mark.parametrize("vendor", ["amd", "intel", "nvidia", None])
def test_whatever_is_returned_is_shaped_like_chrome(host_vendor, vendor):
    """Guards the one mistake the table's comment is about.

    Camoufox's `webgl_data.db` sits one import away and is full of ready-made
    pairs — but every row of it is Gecko's: it suffixes ", or similar" and never
    names the D3D11 backend the way Chrome does. Emitting one from Chrome would
    be a sharper tell than the Intel claim this replaced. Asserted on the
    RETURN VALUE so a function that decorates the string, or reads the Firefox
    table, fails here.
    """
    claimed_vendor, renderer = host_vendor(vendor)

    assert claimed_vendor.startswith("Google Inc. ("), claimed_vendor
    assert renderer.startswith("ANGLE ("), renderer
    assert renderer.endswith(", D3D11)"), "Chrome on Windows names its backend"
    assert ", or similar" not in renderer, "that suffix is Firefox's, not Chrome's"


@pytest.mark.parametrize("vendor,expected", [("amd", AMD), ("intel", INTEL), ("nvidia", INTEL)])
def test_the_factory_carries_the_resolved_identity(host_vendor, vendor, expected):
    """Resolver → Stealth, with a pinned host instead of a live comparison.

    Comparing the module's object against a live resolve is a tautology, and on
    a CI runner whose vendor happens to match the old hardcoded value it passes
    even with the wiring reverted.
    """
    stealth = runner_mod._build_stealth(*host_vendor(vendor))

    assert (stealth.webgl_vendor_override, stealth.webgl_renderer_override) == expected


@pytest.mark.asyncio
async def test_a_real_fetch_patches_the_page_with_the_resolved_identity(monkeypatch):
    """The last link: that `fetch()` reaches for the resolver at all.

    Everything above proves resolver → factory → cached object. None of it
    notices a call site pointed at some OTHER Stealth instance, which is not a
    hypothetical: `src/queue/tasks.py` builds a bare `Stealth()` on the
    login-replay path and gets playwright-stealth's macOS default. So drive the
    real `fetch()` and assert the object it applies is the resolved one.
    """
    from tests.browser.test_selector_timeout_classification import (
        SERP_URL, _page, _playwright_fetch,
    )

    monkeypatch.setattr(fp, "host_facts", lambda: ("linux", "amd"))
    runner_mod.stealth_config.cache_clear()
    applied: list[tuple[str, str]] = []

    async def _record(self, page):  # noqa: ANN001  (patching a library method)
        applied.append((self.webgl_vendor_override, self.webgl_renderer_override))

    monkeypatch.setattr(
        runner_mod.Stealth, "apply_stealth_async", _record, raising=True
    )
    try:
        await _playwright_fetch(
            _page(content="<html>ok</html>", url=SERP_URL, selector_times_out=False),
            stealth=True,
        )
    finally:
        runner_mod.stealth_config.cache_clear()

    assert applied == [AMD]


def test_the_configured_stealth_object_resolves_per_host(monkeypatch):
    """The object pages actually get must come from the resolver, on any runner.

    Pinned to AMD and to a literal, because the value this replaced was the
    Intel pair: comparing against a live resolve instead would pass on a CI
    runner whose own vendor is Intel — or on any host that falls back to Intel —
    even with the wiring torn out. CI is `runs-on: ubuntu-latest`, whose pool
    mixes Intel and AMD, so that test would have been a coin flip.
    """
    monkeypatch.setattr(fp, "host_facts", lambda: ("linux", "amd"))
    runner_mod.stealth_config.cache_clear()
    try:
        stealth = runner_mod.stealth_config()
        assert (stealth.webgl_vendor_override, stealth.webgl_renderer_override) == AMD
    finally:
        # The cache outlives the monkeypatch, so a faked host would otherwise
        # leak into every later test that touches the real Stealth object.
        runner_mod.stealth_config.cache_clear()


def test_the_camoufox_table_is_not_the_chromium_one():
    """Two engines, two corpora — kept apart on purpose.

    Vendor strings legitimately coincide ("Google Inc. (AMD)" is what both
    browsers report through ANGLE), so only the renderers are compared. If those
    ever intersect, one engine is emitting the other's string.
    """
    camoufox_renderers = {r for _, r in fp._WEBGL_BY_OS_AND_VENDOR.values()}
    chromium_renderers = {r for _, r in fp._ANGLE_BY_OS_AND_VENDOR.values()}

    assert not (camoufox_renderers & chromium_renderers)
