from __future__ import annotations

from src.settings import Settings


def test_the_shipped_default_profile_is_host_aligned():
    """Unset means host-aligned, not Camoufox's uniform three-way random.

    The old behaviour is still reachable as the explicit profile `random`; it is
    just no longer what a caller gets by saying nothing.
    """
    s = Settings(cyberyozh_api_key=None)

    assert s.camoufox_fingerprint_profile == "windows_on_host"


def test_the_gpu_vendor_is_unset_until_an_operator_states_it():
    """A detection override.

    Detection is the normal path; this exists because a container cannot see the
    GPU and has to infer it from the CPU vendor, which a discrete card breaks.
    """
    s = Settings(cyberyozh_api_key=None)

    assert s.host_gpu_vendor is None


def test_the_operator_can_state_them(monkeypatch):
    monkeypatch.setenv("HOST_GPU_VENDOR", "nvidia")
    monkeypatch.setenv("CAMOUFOX_FINGERPRINT_PROFILE", "host")

    s = Settings(cyberyozh_api_key=None)

    assert s.host_gpu_vendor == "nvidia"
    assert s.camoufox_fingerprint_profile == "host"
