from __future__ import annotations

import pytest
from unittest.mock import Mock, AsyncMock, patch
from fastapi import FastAPI

from src.app import create_app


class TestAppCreation:
    def test_create_app(self):
        """Create FastAPI App"""
        app = create_app()

        assert isinstance(app, FastAPI)

    def test_app_title(self):
        """Check App title"""
        app = create_app()

        assert app.title == "Open Scraper"

    def test_app_version(self):
        """Check App version"""
        app = create_app()

        assert app.version == "0.2.0"

    def test_app_routers_included(self):
        """Check routers"""
        app = create_app()

        # Check, that there are routes
        routes = [route.path for route in app.routes]

        assert "/api/v1/health" in routes
        assert any("/api/v1/scrape" in route for route in routes)


class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_startup(self, mocker):
        """Test startup in lifespan"""
        # Mock worker_pool
        mock_worker_pool = Mock()
        mock_worker_pool.init = Mock()
        mock_worker_pool.start = Mock()
        mock_worker_pool.stop = Mock()
        mocker.patch("src.app.worker_pool", mock_worker_pool)

        # Mock JobRunner
        mock_job_runner = Mock()
        mock_job_runner.start = AsyncMock()
        mock_job_runner.stop = AsyncMock()
        mocker.patch("src.app.JobRunner", return_value=mock_job_runner)

        # Mock get_job_queue
        mock_queue = Mock()
        mocker.patch("src.app.get_job_queue", return_value=mock_queue)

        # Create app
        with patch("src.app.settings") as mock_settings:
            mock_settings.workers = 2
            mock_settings.queue_maxsize = 100
            mock_settings.job_timeout_ms = 30000
            mock_settings.jobs_enabled = True
            mock_settings.log_level = "INFO"  # Add log_level

            app = create_app()

            # Manual call lifespan
            async with app.router.lifespan_context(app):
                # Check, worker_pool is initialized and was running
                mock_worker_pool.init.assert_called_once()
                mock_worker_pool.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_shutdown(self, mocker):
        """Test shutdown in lifespan"""
        mock_worker_pool = Mock()
        mock_worker_pool.init = Mock()
        mock_worker_pool.start = Mock()
        mock_worker_pool.stop = Mock()
        mocker.patch("src.app.worker_pool", mock_worker_pool)

        mock_job_runner = Mock()
        mock_job_runner.start = AsyncMock()
        mock_job_runner.stop = AsyncMock()
        mocker.patch("src.app.JobRunner", return_value=mock_job_runner)

        mock_queue = Mock()
        mocker.patch("src.app.get_job_queue", return_value=mock_queue)

        with patch("src.app.settings") as mock_settings:
            mock_settings.workers = 2
            mock_settings.queue_maxsize = 100
            mock_settings.job_timeout_ms = 30000
            mock_settings.jobs_enabled = True
            mock_settings.log_level = "INFO"  # Add log_level

            app = create_app()

            async with app.router.lifespan_context(app):
                pass

            # After context exit, stop should be call
            mock_worker_pool.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_jobs_disabled(self, mocker):
        """Test lifespan with disabled jobs"""
        mock_worker_pool = Mock()
        mock_worker_pool.init = Mock()
        mock_worker_pool.start = Mock()
        mock_worker_pool.stop = Mock()
        mocker.patch("src.app.worker_pool", mock_worker_pool)

        mock_job_runner = Mock()
        mock_job_runner.start = AsyncMock()
        mock_job_runner.stop = AsyncMock()
        mocker.patch("src.app.JobRunner", return_value=mock_job_runner)

        mock_queue = Mock()
        mocker.patch("src.app.get_job_queue", return_value=mock_queue)

        with patch("src.app.settings") as mock_settings:
            mock_settings.workers = 2
            mock_settings.queue_maxsize = 100
            mock_settings.job_timeout_ms = 30000
            mock_settings.jobs_enabled = False  # Disable
            mock_settings.log_level = "INFO"  # Add log_level

            app = create_app()

            async with app.router.lifespan_context(app):
                pass

            # job_runner.start must not be call
            mock_job_runner.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_lifespan_worker_pool_config(self, mocker):
        """Check configuration worker_pool from settings"""
        mock_worker_pool = Mock()
        mock_worker_pool.init = Mock()
        mock_worker_pool.start = Mock()
        mock_worker_pool.stop = Mock()
        mocker.patch("src.app.worker_pool", mock_worker_pool)

        mock_job_runner = Mock()
        mock_job_runner.start = AsyncMock()
        mock_job_runner.stop = AsyncMock()
        mocker.patch("src.app.JobRunner", return_value=mock_job_runner)

        mock_queue = Mock()
        mocker.patch("src.app.get_job_queue", return_value=mock_queue)

        # Mock WorkerPoolConfig
        mock_config_class = Mock()
        mocker.patch("src.app.WorkerPoolConfig", mock_config_class)

        with patch("src.app.settings") as mock_settings:
            mock_settings.workers = 4
            mock_settings.queue_maxsize = 200
            mock_settings.job_timeout_ms = 60000
            mock_settings.jobs_enabled = False
            mock_settings.log_level = "DEBUG"  # Add log_level

            app = create_app()

            async with app.router.lifespan_context(app):
                pass

            # Check, WorkerPoolConfig created with correct params
            mock_config_class.assert_called_once_with(
                workers=4,
                queue_maxsize=200,
                job_timeout_ms=60000,
            )


class TestLogging:
    def test_app_logging_setup(self, mocker):
        """Check call setup_logging"""
        mock_setup_logging = mocker.patch("src.app.setup_logging")

        with patch("src.app.settings") as mock_settings:
            mock_settings.log_level = "DEBUG"

            app = create_app()

            # Check setup_logging was called
            mock_setup_logging.assert_called_once_with("DEBUG", tag="M")
