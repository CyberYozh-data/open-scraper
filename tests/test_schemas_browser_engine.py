import pytest
from pydantic import ValidationError
from src.schemas import ScrapeRequest


def test_browser_engine_defaults_to_chromium():
    req = ScrapeRequest(url="https://example.com")
    assert req.browser_engine == "chromium"
    assert req.humanize is False
    assert req.block_webgl is False
    assert req.spoof_os is None
    assert req.addons is None


def test_browser_engine_accepts_camoufox():
    req = ScrapeRequest(url="https://example.com", browser_engine="camoufox")
    assert req.browser_engine == "camoufox"


def test_invalid_engine_rejected():
    with pytest.raises(ValidationError):
        ScrapeRequest(url="https://example.com", browser_engine="links2")


def test_mobile_with_firefox_rejected():
    with pytest.raises(ValidationError, match="mobile.*not supported"):
        ScrapeRequest(url="https://example.com", browser_engine="firefox", device="mobile")


def test_mobile_with_camoufox_rejected():
    with pytest.raises(ValidationError, match="mobile.*not supported"):
        ScrapeRequest(url="https://example.com", browser_engine="camoufox", device="mobile")


def test_mobile_with_chromium_allowed():
    req = ScrapeRequest(url="https://example.com", browser_engine="chromium", device="mobile")
    assert req.device == "mobile"
