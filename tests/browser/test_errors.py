from __future__ import annotations

import pytest

from src.browser.errors import ScrapeError, BlockedOrCaptchaError


class TestScrapeError:
    def test_scrape_error_inheritance(self):
        """ScrapeError inherited from RuntimeError"""
        assert issubclass(ScrapeError, RuntimeError)

    def test_scrape_error_message(self):
        """ScrapeError with message"""
        error = ScrapeError("Test error message")
        assert str(error) == "Test error message"

    def test_scrape_error_raising(self):
        """Raise and catch ScrapeError"""
        with pytest.raises(ScrapeError) as exc_info:
            raise ScrapeError("Custom error")

        assert "Custom error" in str(exc_info.value)


class TestBlockedOrCaptchaError:
    def test_blocked_or_captcha_error_inheritance(self):
        """BlockedOrCaptchaError inherited from ScrapeError"""
        assert issubclass(BlockedOrCaptchaError, ScrapeError)
        assert issubclass(BlockedOrCaptchaError, RuntimeError)

    def test_blocked_or_captcha_error_message(self):
        """BlockedOrCaptchaError with message"""
        error = BlockedOrCaptchaError("Captcha detected")
        assert str(error) == "Captcha detected"

    def test_blocked_or_captcha_error_raising(self):
        """Raise and catch BlockedOrCaptchaError"""
        with pytest.raises(BlockedOrCaptchaError) as exc_info:
            raise BlockedOrCaptchaError("Access denied")

        assert "Access denied" in str(exc_info.value)

    def test_catch_as_scrape_error(self):
        """BlockedOrCaptchaError must catch for ScrapeError"""
        with pytest.raises(ScrapeError):
            raise BlockedOrCaptchaError("Test")

    def test_catch_as_runtime_error(self):
        """BlockedOrCaptchaError must catch for RuntimeError"""
        with pytest.raises(RuntimeError):
            raise BlockedOrCaptchaError("Test")
