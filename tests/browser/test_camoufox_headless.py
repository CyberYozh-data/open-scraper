from src.browser.camoufox_runner import CamoufoxRunner, build_camoufox_options
from src.settings import settings


def _opts(**kw):
    return build_camoufox_options(
        proxy=None, block_assets=False, webrtc_block=False, **kw
    )


def test_options_default_to_server_setting():
    assert _opts()["headless"] is settings.headless


def test_options_honour_requested_launch_mode():
    """headless=False must reach Camoufox, not be silently ignored."""
    assert _opts(headless=False)["headless"] is False
    assert _opts(headless=True)["headless"] is True


def test_runner_defaults_to_server_setting():
    assert CamoufoxRunner(timeout_ms=1000)._headless is settings.headless


def test_runner_stores_requested_launch_mode():
    assert CamoufoxRunner(timeout_ms=1000, headless=False)._headless is False
