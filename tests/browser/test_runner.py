from __future__ import annotations

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, call, patch
import base64

from src.browser.runner import PlaywrightRunner, FetchResult, DESKTOP, MOBILE
from src.proxy.models import ProxyConfig
from playwright.async_api import Error as PWError


class TestPlaywrightRunner:
    def test_runner_init(self):
        """Initializing runner"""
        runner = PlaywrightRunner(
            headless=True,
            block_assets=False,
            timeout_ms=30000,
        )

        assert runner.headless is True
        assert runner.block_assets is False
        assert runner.timeout_ms == 30000
        assert runner._browser is None
        assert runner._playwright is None

    @pytest.mark.asyncio
    async def test_runner_start(self):
        """Start browser"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        # Mock playwright
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_playwright.stop = AsyncMock()

        # Call mock for async_playwright()
        mock_async_playwright_instance = MagicMock()
        mock_async_playwright_instance.start = AsyncMock(return_value=mock_playwright)
        mock_async_playwright_instance.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_async_playwright_instance.__aexit__ = AsyncMock()

        mock_async_playwright = MagicMock(return_value=mock_async_playwright_instance)

        with patch("src.browser.runner.async_playwright", mock_async_playwright):
            await runner.start()

            assert runner._browser == mock_browser
            assert runner._playwright == mock_playwright
            mock_playwright.chromium.launch.assert_called_once_with(headless=True)

            # Cleanup
            await runner.stop()

    @pytest.mark.asyncio
    async def test_runner_stop(self):
        """Browser stop"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        # Set mock
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()
        runner._browser = mock_browser
        runner._playwright = mock_playwright

        await runner.stop()

        mock_browser.close.assert_called_once()
        mock_playwright.stop.assert_called_once()
        assert runner._browser is None
        assert runner._playwright is None

    @pytest.mark.asyncio
    async def test_runner_start_idempotent(self):
        """Double start not create new browser"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_playwright.stop = AsyncMock()

        mock_async_playwright_instance = MagicMock()
        mock_async_playwright_instance.start = AsyncMock(return_value=mock_playwright)
        mock_async_playwright = MagicMock(return_value=mock_async_playwright_instance)

        with patch("src.browser.runner.async_playwright", mock_async_playwright):
            await runner.start()
            await runner.start()

            # start() must be call only one time (because have if self._browser is not None)
            assert mock_async_playwright_instance.start.call_count == 1

            await runner.stop()


class TestFetch:
    @pytest.mark.asyncio
    async def test_fetch_success_basic(self):
        """Base fetch is success"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        # Set _browser, for go around start()
        mock_browser = AsyncMock()
        runner._browser = mock_browser

        mock_page = AsyncMock()
        mock_response = Mock()
        mock_response.status = 200
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.content = AsyncMock(return_value="<html><body>Test</body></html>")
        mock_page.url = "https://example.com"
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(runner, "_new_context", return_value=mock_context):
            result = await runner.fetch(
                url="https://example.com",
                device="desktop",
                proxy=None,
                headers=None,
                wait_until="domcontentloaded",
                wait_for_selector=None,
                timeout_ms=None,
                screenshot=False,
            )

        assert result.ok is True
        assert result.html == "<html><body>Test</body></html>"
        assert result.final_url == "https://example.com"
        assert result.status_code == 200
        assert result.screenshot_b64 is None
        assert result.error is None

    @pytest.mark.asyncio
    async def test_fetch_with_proxy(self):
        """Fetch with proxy"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
        runner._browser = AsyncMock()

        proxy = ProxyConfig(
            server="http://proxy.com:8080",
            username="user",
            password="pass",
        )

        mock_page = AsyncMock()
        mock_response = Mock()
        mock_response.status = 200
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.content = AsyncMock(return_value="<html></html>")
        mock_page.url = "https://example.com"
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_new_context = AsyncMock(return_value=mock_context)

        with patch.object(runner, "_new_context", mock_new_context):
            await runner.fetch(
                url="https://example.com",
                device="desktop",
                proxy=proxy,
                headers=None,
                wait_until="domcontentloaded",
                wait_for_selector=None,
                timeout_ms=None,
                screenshot=False,
            )

        # Check, _new_context was called with proxy
        mock_new_context.assert_called_once()
        call_kwargs = mock_new_context.call_args[1]
        assert call_kwargs["proxy"] == proxy

    @pytest.mark.asyncio
    async def test_fetch_with_headers(self):
        """Fetch with custom headers"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
        runner._browser = AsyncMock()

        headers = {"User-Agent": "Custom UA", "Accept-Language": "en-US"}

        mock_page = AsyncMock()
        mock_response = Mock()
        mock_response.status = 200
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.content = AsyncMock(return_value="<html></html>")
        mock_page.url = "https://example.com"
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_new_context = AsyncMock(return_value=mock_context)

        with patch.object(runner, "_new_context", mock_new_context):
            await runner.fetch(
                url="https://example.com",
                device="desktop",
                proxy=None,
                headers=headers,
                wait_until="domcontentloaded",
                wait_for_selector=None,
                timeout_ms=None,
                screenshot=False,
            )

        # Check, headers send
        call_kwargs = mock_new_context.call_args[1]
        assert call_kwargs["headers"] == headers

    @pytest.mark.asyncio
    async def test_fetch_with_wait_for_selector(self):
        """Fetch with wait_for_selector"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
        runner._browser = AsyncMock()

        mock_page = AsyncMock()
        mock_response = Mock()
        mock_response.status = 200
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.wait_for_selector = AsyncMock()
        mock_page.content = AsyncMock(return_value="<html></html>")
        mock_page.url = "https://example.com"
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(runner, "_new_context", return_value=mock_context):
            await runner.fetch(
                url="https://example.com",
                device="desktop",
                proxy=None,
                headers=None,
                wait_until="domcontentloaded",
                wait_for_selector=".content",
                timeout_ms=30000,
                screenshot=False,
            )

        mock_page.wait_for_selector.assert_called_once_with(".content", timeout=30000)

    @pytest.mark.asyncio
    async def test_fetch_with_screenshot(self):
        """Fetch with screenshot"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
        runner._browser = AsyncMock()

        screenshot_bytes = b"fake_png_data"

        mock_page = AsyncMock()
        mock_response = Mock()
        mock_response.status = 200
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.content = AsyncMock(return_value="<html></html>")
        mock_page.url = "https://example.com"
        mock_page.screenshot = AsyncMock(return_value=screenshot_bytes)
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(runner, "_new_context", return_value=mock_context):
            result = await runner.fetch(
                url="https://example.com",
                device="desktop",
                proxy=None,
                headers=None,
                wait_until="domcontentloaded",
                wait_for_selector=None,
                timeout_ms=None,
                screenshot=True,
            )

        assert result.screenshot_b64 is not None
        assert result.screenshot_b64 == base64.b64encode(screenshot_bytes).decode("ascii")
        mock_page.screenshot.assert_called_once_with(full_page=True)

    @pytest.mark.asyncio
    async def test_fetch_mobile_device(self):
        """Fetch for mobile"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
        runner._browser = AsyncMock()

        mock_page = AsyncMock()
        mock_response = Mock()
        mock_response.status = 200
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.content = AsyncMock(return_value="<html></html>")
        mock_page.url = "https://example.com"
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_new_context = AsyncMock(return_value=mock_context)

        with patch.object(runner, "_new_context", mock_new_context):
            await runner.fetch(
                url="https://example.com",
                device="mobile",
                proxy=None,
                headers=None,
                wait_until="domcontentloaded",
                wait_for_selector=None,
                timeout_ms=None,
                screenshot=False,
            )

        # Check, that device send
        call_kwargs = mock_new_context.call_args[1]
        assert call_kwargs["device"] == "mobile"

    @pytest.mark.asyncio
    async def test_fetch_timeout_error(self):
        """Fetch with timeout"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
        runner._browser = AsyncMock()

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(side_effect=PWError("Timeout exceeded"))
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(runner, "_new_context", return_value=mock_context):
            result = await runner.fetch(
                url="https://example.com",
                device="desktop",
                proxy=None,
                headers=None,
                wait_until="domcontentloaded",
                wait_for_selector=None,
                timeout_ms=1000,
                screenshot=False,
            )

        assert result.ok is False
        assert result.error is not None
        assert "PlaywrightError" in result.error
        assert "Timeout" in result.error

    @pytest.mark.asyncio
    async def test_fetch_captcha_detection(self):
        """Found captcha"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
        runner._browser = AsyncMock()

        captcha_html = "<html><body>Please verify you are a human</body></html>"

        mock_page = AsyncMock()
        mock_response = Mock()
        mock_response.status = 200
        mock_page.goto = AsyncMock(return_value=mock_response)
        mock_page.content = AsyncMock(return_value=captcha_html)
        mock_page.url = "https://example.com"
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(runner, "_new_context", return_value=mock_context):
            result = await runner.fetch(
                url="https://example.com",
                device="desktop",
                proxy=None,
                headers=None,
                wait_until="domcontentloaded",
                wait_for_selector=None,
                timeout_ms=None,
                screenshot=False,
            )

        assert result.ok is False
        assert "Captcha/block" in result.error

    @pytest.mark.asyncio
    async def test_fetch_network_error(self):
        """Network error during fetch"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
        runner._browser = AsyncMock()

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(side_effect=PWError("net::ERR_CONNECTION_REFUSED"))
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(runner, "_new_context", return_value=mock_context):
            result = await runner.fetch(
                url="https://example.com",
                device="desktop",
                proxy=None,
                headers=None,
                wait_until="domcontentloaded",
                wait_for_selector=None,
                timeout_ms=None,
                screenshot=False,
            )

        assert result.ok is False
        assert "PlaywrightError" in result.error
        assert "ERR_CONNECTION_REFUSED" in result.error


class TestNewContext:
    @pytest.mark.asyncio
    async def test_new_context_desktop(self):
        """Created context for desktop"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        runner._browser = mock_browser

        context = await runner._new_context(
            device="desktop",
            proxy=None,
            headers=None,
        )

        assert context == mock_context
        call_kwargs = mock_browser.new_context.call_args[1]
        assert call_kwargs["user_agent"] == DESKTOP["user_agent"]
        assert call_kwargs["viewport"] == DESKTOP["viewport"]

    @pytest.mark.asyncio
    async def test_new_context_mobile(self):
        """Create context for mobile"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        runner._browser = mock_browser

        context = await runner._new_context(
            device="mobile",
            proxy=None,
            headers=None,
        )

        call_kwargs = mock_browser.new_context.call_args[1]
        assert call_kwargs["user_agent"] == MOBILE["user_agent"]
        assert call_kwargs["viewport"] == MOBILE["viewport"]

    @pytest.mark.asyncio
    async def test_new_context_with_proxy(self):
        """Create context with proxy"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        proxy = ProxyConfig(
            server="http://proxy.com:8080",
            username="user",
            password="pass",
        )

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        runner._browser = mock_browser

        await runner._new_context(
            device="desktop",
            proxy=proxy,
            headers=None,
        )

        call_kwargs = mock_browser.new_context.call_args[1]
        proxy_arg = call_kwargs["proxy"]
        assert proxy_arg["server"] == "http://proxy.com:8080"
        assert proxy_arg["username"] == "user"
        assert proxy_arg["password"] == "pass"

    @pytest.mark.asyncio
    async def test_new_context_with_headers(self):
        """Create context with headers"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        headers = {"Custom-Header": "Value"}

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        runner._browser = mock_browser

        await runner._new_context(
            device="desktop",
            proxy=None,
            headers=headers,
        )

        call_kwargs = mock_browser.new_context.call_args[1]
        assert call_kwargs["extra_http_headers"] == headers

    @pytest.mark.asyncio
    async def test_new_context_block_assets(self):
        """Blocking assets in context"""
        runner = PlaywrightRunner(headless=True, block_assets=True, timeout_ms=30000)

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.route = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        runner._browser = mock_browser

        await runner._new_context(
            device="desktop",
            proxy=None,
            headers=None,
        )

        # Check, route was configurated
        mock_context.route.assert_called_once()


class TestCaptchaDetection:
    def test_looks_like_captcha_detection_captcha_keyword(self):
        """Detecting captcha by word"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        html = "<html><body>Please solve the CAPTCHA</body></html>"
        assert runner._looks_like_captcha_or_block(html) is True

    def test_looks_like_captcha_detection_unusual_traffic(self):
        """Detecting by unusual traffic"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        html = "<html><body>Unusual traffic from your network</body></html>"
        assert runner._looks_like_captcha_or_block(html) is True

    def test_looks_like_captcha_detection_verify_human(self):
        """Detecting by verify you are a human"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        html = "<html><body>Please verify you are a human</body></html>"
        assert runner._looks_like_captcha_or_block(html) is True

    def test_looks_like_captcha_detection_access_denied(self):
        """Detecting by access denied"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        html = "<html><body>Access Denied</body></html>"
        assert runner._looks_like_captcha_or_block(html) is True

    def test_looks_like_captcha_detection_clean_html(self):
        """Clear HTML without blocking sings"""
        runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)

        html = "<html><body><h1>Welcome</h1><p>Content</p></body></html>"
        assert runner._looks_like_captcha_or_block(html) is False
