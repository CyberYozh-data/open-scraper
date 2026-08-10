from __future__ import annotations

import logging

import pytest

from src.browser import fingerprint_profile as fp


@pytest.fixture(autouse=True)
def _forget_host_facts():
    """host_facts() is cached; each test states its own host."""
    fp.host_facts.cache_clear()
    yield
    fp.host_facts.cache_clear()


@pytest.fixture
def amd_linux_host(monkeypatch):
    """This server: AMD Ryzen with integrated Radeon, Ubuntu."""
    monkeypatch.setattr(fp.platform, "system", lambda: "Linux")
    monkeypatch.setattr(fp, "_read_cpu_vendor", lambda: "amd")
    monkeypatch.setattr(fp.settings, "host_gpu_vendor", None)


def test_windows_on_host_claims_windows_on_this_machines_hardware(amd_linux_host):
    """The point of the profile: a common OS, this host's hardware.

    Claiming macOS on an AMD x86 box is the case this exists to stop — Camoufox
    pairs that claim with an Apple M1 GPU string on hardware that has never been
    near one.
    """
    r = fp.resolve_fingerprint("windows_on_host")

    assert r.spoof_os == "windows"
    assert r.webgl_config == (
        "Google Inc. (AMD)",
        "ANGLE (AMD, Radeon R9 200 Series Direct3D11 vs_5_0 ps_5_0), or similar",
    )


def test_host_claims_the_host_os(amd_linux_host):
    r = fp.resolve_fingerprint("host")

    assert r.spoof_os == "linux"
    assert r.webgl_config == ("AMD", "Radeon R9 200 Series, or similar")


def test_random_pins_nothing(amd_linux_host):
    """The old behaviour stays reachable, and reachable means pinning NOTHING.

    A half-pinned 'random' would be a fourth behaviour nobody asked for, and the
    benchmark's control arm would stop being a control.
    """
    r = fp.resolve_fingerprint("random")

    assert r.spoof_os is None
    assert r.webgl_config is None


@pytest.mark.parametrize("name", ["windows", "macos", "linux"])
def test_a_bare_os_name_is_todays_spoof_os_and_pins_nothing_else(amd_linux_host, name):
    """`spoof_os` maps onto these, so they must not quietly gain hardware pins.

    A caller who has been sending spoof_os='windows' for months must keep
    getting what they got, or this is a silent behaviour change wearing a new
    field's name.
    """
    r = fp.resolve_fingerprint(name)

    assert r.spoof_os == name
    assert r.webgl_config is None


def test_auto_follows_the_setting(amd_linux_host, monkeypatch):
    monkeypatch.setattr(fp.settings, "camoufox_fingerprint_profile", "host")

    assert fp.resolve_fingerprint("auto").spoof_os == "linux"
    assert fp.resolve_fingerprint(None).spoof_os == "linux"


def test_auto_pointing_at_auto_is_not_an_infinite_loop(amd_linux_host, monkeypatch):
    """A circular setting must degrade to the safe end, not recurse."""
    monkeypatch.setattr(fp.settings, "camoufox_fingerprint_profile", "auto")

    r = fp.resolve_fingerprint("auto")

    assert r.profile == "random"
    assert r.spoof_os is None


def test_an_unknown_setting_falls_back_to_random_and_says_so(
    amd_linux_host, monkeypatch, caplog
):
    monkeypatch.setattr(fp.settings, "camoufox_fingerprint_profile", "wintendo")

    with caplog.at_level(logging.WARNING, logger="src.browser.fingerprint_profile"):
        r = fp.resolve_fingerprint("auto")

    assert r.profile == "random"
    assert "wintendo" in caplog.text


def test_a_vendor_with_no_pair_leaves_webgl_alone_rather_than_raising(monkeypatch):
    """An un-pinned fingerprint is today's behaviour; failing the scrape is not.

    macOS has no NVIDIA entry in Camoufox's table — Apple stopped shipping them —
    so this combination has to degrade, not raise, and the rest of the profile
    still applies.
    """
    monkeypatch.setattr(fp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(fp.settings, "host_gpu_vendor", "nvidia")

    r = fp.resolve_fingerprint("host")

    assert r.spoof_os == "macos"
    assert r.webgl_config is None


def test_an_unknown_host_os_cannot_be_claimed_honestly(monkeypatch):
    """`host` on a platform Camoufox cannot spoof has nothing honest to say."""
    monkeypatch.setattr(fp.platform, "system", lambda: "SunOS")
    monkeypatch.setattr(fp, "_read_cpu_vendor", lambda: None)
    monkeypatch.setattr(fp.settings, "host_gpu_vendor", None)

    r = fp.resolve_fingerprint("host")

    assert r.profile == "random"
    assert r.spoof_os is None


def test_windows_on_host_survives_an_unknown_host_os(monkeypatch):
    """The OS is stated by the profile, not detected, so only the hardware is
    at the mercy of detection."""
    monkeypatch.setattr(fp.platform, "system", lambda: "SunOS")
    monkeypatch.setattr(fp, "_read_cpu_vendor", lambda: "intel")
    monkeypatch.setattr(fp.settings, "host_gpu_vendor", None)

    r = fp.resolve_fingerprint("windows_on_host")

    assert r.spoof_os == "windows"
    assert r.webgl_config == (
        "Google Inc. (Intel)",
        "ANGLE (Intel, Intel(R) HD Graphics Direct3D11 vs_5_0 ps_5_0), or similar",
    )


def test_no_profile_pins_navigator_properties(amd_linux_host):
    """Measured, not assumed: pinning navigator.hardwareConcurrency HURT.

    On yandex_search, 20 runs per arm, it took a Windows claim from 1 retry to 8
    and a Linux one from 15 to 23; dropping it brought the Windows arm level with
    an unpinned control (3 vs 3). It was pointless as well as costly — Camoufox
    already spoofs the value, so the host's real count never leaked — and it is
    the one knob Camoufox's own guard rail warns about.

    This test exists so the idea does not come back without new numbers.
    """
    for name in fp.FINGERPRINT_PROFILES:
        resolved = fp.resolve_fingerprint(name)
        assert not hasattr(resolved, "config"), name


def test_an_operator_override_wins_over_detection(monkeypatch):
    """The discrete-GPU case: the CPU says AMD, the card is NVIDIA."""
    monkeypatch.setattr(fp.platform, "system", lambda: "Linux")
    monkeypatch.setattr(fp, "_read_cpu_vendor", lambda: "amd")
    monkeypatch.setattr(fp.settings, "host_gpu_vendor", "nvidia")

    r = fp.resolve_fingerprint("host")

    assert r.webgl_config == ("NVIDIA Corporation", "NVIDIA GeForce GTX 980, or similar")


def test_an_apple_host_needs_no_proc_to_know_its_gpu_vendor(monkeypatch):
    """There is no /proc on macOS, and no ambiguity about the vendor either."""
    monkeypatch.setattr(fp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(fp, "_read_cpu_vendor", lambda: None)
    monkeypatch.setattr(fp.settings, "host_gpu_vendor", None)

    assert fp.resolve_fingerprint("host").webgl_config == ("Apple", "Apple M1, or similar")


def test_the_applied_profile_is_reportable(amd_linux_host):
    """`meta.applied_fingerprint` is how a scrape gets diagnosed from its
    response instead of from a worker log nobody kept."""
    meta = fp.resolve_fingerprint("windows_on_host").as_meta()

    assert meta == {
        "profile": "windows_on_host",
        "os": "windows",
        "webgl_vendor": "Google Inc. (AMD)",
        "webgl_renderer": (
            "ANGLE (AMD, Radeon R9 200 Series Direct3D11 vs_5_0 ps_5_0), or similar"
        ),
    }


def test_random_reports_that_it_pinned_nothing(amd_linux_host):
    """A degraded profile must be distinguishable from one that applied."""
    assert fp.resolve_fingerprint("random").as_meta() == {
        "profile": "random",
        "os": None,
        "webgl_vendor": None,
        "webgl_renderer": None,
    }


def test_every_profile_name_the_api_offers_resolves(amd_linux_host):
    """FINGERPRINT_PROFILES is what the schema Literal and the UI select are
    built from, so a name in it that falls through to the unknown branch would
    be an advertised option that silently does nothing."""
    for name in fp.FINGERPRINT_PROFILES:
        resolved = fp.resolve_fingerprint(name)
        assert resolved.profile != "random" or name in ("random", "auto"), name
