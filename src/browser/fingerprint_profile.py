"""Resolve a named fingerprint profile into Camoufox launch options.

Camoufox generates its fingerprint per launch and draws every part nobody
pinned from its own corpus: the OS uniformly from windows/macos/linux, the GPU
and the thread count from a weighted table. Each result is a machine that could
exist. None of them is the machine we are on, and the properties Camoufox does
not spoof keep coming from the real one — which is how a launch ends up
claiming an Apple M1 while running on AMD x86 with zero speech-synthesis
voices, a combination no Mac has ever had.

The job here is to stop volunteering contradictions, not to force the truth: a
profile decides how far from the host to sit, and the operator keeps the choice.

WHAT A PROFILE PINS IS DELIBERATELY NARROW: the OS, and the WebGL vendor pair
that goes with it. Nothing else, because nothing else survived measurement.
Pinning `navigator.hardwareConcurrency` to the host's real count was in the
first draft and made things measurably WORSE — on yandex_search over 20 runs
per arm it took a Windows claim from 1 retry to 8, and a Linux one from 15 to
23; removing it brought the Windows arm back level with an unpinned control
(3 vs 3). It was also pointless: Camoufox already spoofs that value (2, 4, 8,
12 and 16 all observed on this 16-thread host), so the real count never leaked.
It is the one knob Camoufox's own guard rail warns about, and the guard rail
was right.

The same runs measured the OS claim itself: Windows beat Linux clearly (1 and 8
retries against 15 and 23), so claiming this Linux host honestly is the worst of
the options, not the safest. Desktop Linux is rare enough to be a signal.
"""
from __future__ import annotations

import logging
import platform
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from src.settings import settings

log = logging.getLogger(__name__)

FingerprintProfile = Literal[
    "auto", "host", "windows_on_host", "random", "windows", "macos", "linux"
]

# The same names, in the order the UI offers them. Data rather than a second
# hand-written list, so the schema Literal, the select and the tests cannot
# drift apart.
FINGERPRINT_PROFILES: tuple[str, ...] = (
    "auto", "host", "windows_on_host", "random", "windows", "macos", "linux",
)

# The bare OS names — exactly `spoof_os`'s domain, which maps onto them.
_BARE_OS_PROFILES: frozenset[str] = frozenset({"windows", "macos", "linux"})

_PLATFORM_TO_OS: dict[str, str] = {
    "Linux": "linux",
    "Windows": "windows",
    "Darwin": "macos",
}

_CPU_VENDOR_TO_GPU_VENDOR: dict[str, str] = {
    "AuthenticAMD": "amd",
    "GenuineIntel": "intel",
}

# (target OS, GPU vendor) -> (WebGL vendor, WebGL renderer).
#
# Every pair is copied verbatim from Camoufox's own `webgl_data.db`, because
# `webgl_config` is validated against it: an invented string raises ValueError
# at launch, i.e. in production on the first scrape that picks the profile.
# `tests/browser/test_fingerprint_profile_e2e.py` re-checks the whole map
# against that table, so a Camoufox bump that rewrites it fails there instead.
#
# What AMD offers is thin — an R9 200 (2013) and an HD 3200 (2008), nothing
# newer, on any OS. That is the table's ceiling, not a preference.
_WEBGL_BY_OS_AND_VENDOR: dict[tuple[str, str], tuple[str, str]] = {
    ("windows", "amd"): (
        "Google Inc. (AMD)",
        "ANGLE (AMD, Radeon R9 200 Series Direct3D11 vs_5_0 ps_5_0), or similar",
    ),
    ("windows", "intel"): (
        "Google Inc. (Intel)",
        "ANGLE (Intel, Intel(R) HD Graphics Direct3D11 vs_5_0 ps_5_0), or similar",
    ),
    ("windows", "nvidia"): (
        "Google Inc. (NVIDIA)",
        "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar",
    ),
    ("linux", "amd"): ("AMD", "Radeon R9 200 Series, or similar"),
    ("linux", "intel"): ("Intel", "Intel(R) HD Graphics, or similar"),
    ("linux", "nvidia"): ("NVIDIA Corporation", "NVIDIA GeForce GTX 980, or similar"),
    ("macos", "apple"): ("Apple", "Apple M1, or similar"),
    ("macos", "amd"): ("ATI Technologies Inc.", "Radeon R9 200 Series, or similar"),
    ("macos", "intel"): ("Intel Inc.", "Intel(R) HD Graphics, or similar"),
}


@dataclass(frozen=True)
class ResolvedFingerprint:
    """What a profile name means, in Camoufox's own vocabulary.

    The screen floor is deliberately absent: `build_camoufox_options` derives it
    from the requested viewport, and a second source for one option is how the
    two come to disagree.
    """

    profile: str
    spoof_os: str | None = None
    webgl_config: tuple[str, str] | None = None

    def as_meta(self) -> dict[str, Any]:
        """The response-facing shape, for `meta.applied_fingerprint`."""
        vendor, renderer = self.webgl_config or (None, None)
        return {
            "profile": self.profile,
            "os": self.spoof_os,
            "webgl_vendor": vendor,
            "webgl_renderer": renderer,
        }


def _read_cpu_vendor() -> str | None:
    """The GPU vendor, inferred from the CPU's.

    The container has no view of the GPU, and on integrated graphics — which is
    what this host has — the CPU vendor IS the GPU vendor. On a discrete card it
    is wrong, which is what HOST_GPU_VENDOR is for.
    """
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^vendor_id\s*:\s*(\S+)", text, re.MULTILINE)
    if match is None:
        return None
    return _CPU_VENDOR_TO_GPU_VENDOR.get(match.group(1))


@lru_cache(maxsize=1)
def host_facts() -> tuple[str | None, str | None]:
    """`(os_family, gpu_vendor)` for this machine.

    Cached: neither changes while the process lives, and every host-aligned
    launch would otherwise re-read /proc.
    """
    os_family = _PLATFORM_TO_OS.get(platform.system())

    gpu_vendor = settings.host_gpu_vendor
    if not gpu_vendor:
        gpu_vendor = _read_cpu_vendor()
        # An Apple host's GPU vendor is not in /proc — there is no /proc — and
        # there is no ambiguity about it either.
        if gpu_vendor is None and os_family == "macos":
            gpu_vendor = "apple"

    return os_family, gpu_vendor


def _host_aligned(profile: str, target_os: str | None) -> ResolvedFingerprint:
    """A named OS wearing this host's hardware."""
    _, gpu_vendor = host_facts()
    if target_os is None:
        log.warning(
            "fingerprint profile %r wants this host's OS family, but %r is not "
            "one Camoufox can spoof; leaving the fingerprint to Camoufox",
            profile, platform.system(),
        )
        return ResolvedFingerprint(profile="random")

    webgl_config = None
    if gpu_vendor is not None:
        webgl_config = _WEBGL_BY_OS_AND_VENDOR.get((target_os, gpu_vendor))
    if webgl_config is None:
        # Not an error. An un-pinned WebGL string is exactly today's behaviour,
        # and failing a scrape over a missing table row would cost more than the
        # leak it was meant to close.
        log.info(
            "fingerprint profile %r: Camoufox's WebGL table has no %s entry for "
            "%s; leaving the GPU to Camoufox, so only the OS is stated",
            profile, gpu_vendor or "detected-vendor", target_os,
        )

    return ResolvedFingerprint(
        profile=profile,
        spoof_os=target_os,
        webgl_config=webgl_config,
    )


def claimed_os(profile: str | None) -> str | None:
    """Which OS a profile name claims, or None if it claims none.

    `windows_on_host` and `windows` both claim Windows — they differ in what
    else they pin, not in the OS — so anything comparing the two channels has to
    compare this rather than the literal strings. Routed through
    `resolve_fingerprint` so it cannot drift from what actually launches.
    """
    if profile is None:
        return None
    return resolve_fingerprint(profile).spoof_os


def resolve_fingerprint(profile: str | None) -> ResolvedFingerprint:
    """Turn a profile name (or None) into Camoufox launch options.

    None means `auto`, which means whatever CAMOUFOX_FINGERPRINT_PROFILE names.
    Anything unrecognised — including a circular `auto` in that setting —
    degrades to `random`, the behaviour that shipped before profiles existed,
    and says so in the log.
    """
    name = profile or "auto"

    if name == "auto":
        name = settings.camoufox_fingerprint_profile
        if name == "auto":
            log.warning(
                "CAMOUFOX_FINGERPRINT_PROFILE is 'auto', which points at itself; "
                "using 'random'"
            )
            name = "random"

    if name == "random":
        return ResolvedFingerprint(profile="random")
    if name in _BARE_OS_PROFILES:
        # Exactly `spoof_os`: the OS is stated, the hardware stays Camoufox's.
        return ResolvedFingerprint(profile=name, spoof_os=name)
    if name == "windows_on_host":
        return _host_aligned(name, "windows")
    if name == "host":
        return _host_aligned(name, host_facts()[0])

    log.warning(
        "unknown fingerprint profile %r; using 'random'. Known profiles: %s",
        name, ", ".join(FINGERPRINT_PROFILES),
    )
    return ResolvedFingerprint(profile="random")
