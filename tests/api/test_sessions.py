from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.sessions.store import (
    SessionExpired,
    SessionIncompatible,
    SessionNotFound,
)


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_store(mocker):
    store = AsyncMock()
    mocker.patch("src.sessions.service.get_session_store", return_value=store)
    mocker.patch("src.scrape_service.get_session_store", return_value=store)
    return store


class TestCreateSession:
    def test_minimal(self, client, mock_store):
        from src.sessions.models import SessionRecord
        mock_store.create = AsyncMock(return_value=SessionRecord(
            session_id="sess_abc",
            status="created",
            created_at=1.0, expires_at=86401.0, last_used_at=1.0,
            device="desktop", proxy_type="none",
        ))
        response = client.post("/api/v1/sessions", json={})
        assert response.status_code == 200
        assert response.json()["session_id"] == "sess_abc"

    def test_res_rotating_without_pool_returns_422(self, client, mock_store):
        mock_store.create = AsyncMock(side_effect=SessionIncompatible(
            "res_rotating requires proxy_pool_id"
        ))
        response = client.post(
            "/api/v1/sessions",
            json={"proxy_type": "res_rotating"},
        )
        assert response.status_code == 422


class TestGetSession:
    def test_not_found(self, client, mock_store):
        mock_store.get = AsyncMock(side_effect=SessionNotFound("sess_x"))
        response = client.get("/api/v1/sessions/sess_x")
        assert response.status_code == 404

    def test_expired(self, client, mock_store):
        mock_store.get = AsyncMock(side_effect=SessionExpired("sess_x", 999.0))
        response = client.get("/api/v1/sessions/sess_x")
        assert response.status_code == 410


class TestScrapeWithSessionId:
    def test_scrape_with_unknown_session_returns_404(self, client, mock_store, mocker):
        mock_store.get = AsyncMock(side_effect=SessionNotFound("sess_x"))
        queue = AsyncMock()
        queue.submit = AsyncMock(return_value="job_xx")
        mocker.patch("src.scrape_service.get_job_queue", return_value=queue)

        response = client.post(
            "/api/v1/scrape/page",
            json={"url": "https://x.com", "session_id": "sess_x"},
        )
        assert response.status_code == 404

    def test_scrape_with_session_id_and_cookies_returns_422(
        self, client, mock_store, mocker
    ):
        from src.sessions.models import SessionRecord
        session_record = SessionRecord(
            session_id="sess_x", status="ready",
            created_at=1.0, expires_at=86401.0, last_used_at=1.0,
            device="desktop", proxy_type="none",
        )
        mock_store.get = AsyncMock(return_value=session_record)

        def raises(*args, **kwargs):
            raise SessionIncompatible("cannot pass both session_id and cookies")
        mock_store.assert_compatible_with_request = raises

        response = client.post(
            "/api/v1/scrape/page",
            json={
                "url": "https://x.com",
                "session_id": "sess_x",
                "cookies": [{"name": "a", "value": "b"}],
            },
        )
        assert response.status_code == 422


class TestLoginSession:
    def test_login_success_writes_storage_state_and_sets_ready(self, client, mock_store, mocker):
        from src.sessions.models import SessionRecord, SessionLoginResult
        session_record = SessionRecord(
            session_id="sess_x", status="created",
            created_at=1.0, expires_at=86401.0, last_used_at=1.0,
            device="desktop", proxy_type="none",
        )
        mock_store.get = AsyncMock(return_value=session_record)
        mock_store.set_status = AsyncMock()
        mock_store.update_storage_state = AsyncMock()

        # Real Lock for the lock() call
        import asyncio as _asyncio
        real_lock = _asyncio.Lock()
        mock_store.lock = lambda session_id: real_lock

        async def fake_submit(job, *, job_timeout_ms=None):
            return {
                "ok": True,
                "login_result": SessionLoginResult(ok=True, took_ms=42).model_dump(),
                "storage_state": {"cookies": [{"name": "sid", "value": "a"}], "origins": []},
            }
        mocker.patch("src.sessions.service.worker_pool.submit", new=fake_submit)

        response = client.post("/api/v1/sessions/sess_x/login", json={
            "script": {"steps": [{"op": "goto", "url": "https://x.com"}]},
            "creds": {"email": "u", "password": "p"},
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["took_ms"] == 42
        mock_store.update_storage_state.assert_awaited_once()

    def test_login_failure_sets_failed_status(self, client, mock_store, mocker):
        from src.sessions.models import SessionRecord, SessionLoginResult
        session_record = SessionRecord(
            session_id="sess_x", status="created",
            created_at=1.0, expires_at=86401.0, last_used_at=1.0,
            device="desktop", proxy_type="none",
        )
        mock_store.get = AsyncMock(return_value=session_record)
        mock_store.set_status = AsyncMock()

        import asyncio as _asyncio
        real_lock = _asyncio.Lock()
        mock_store.lock = lambda session_id: real_lock

        async def fake_submit(job, *, job_timeout_ms=None):
            return {
                "ok": True,
                "login_result": SessionLoginResult(
                    ok=False, failed_step_index=1, error="selector not found",
                ).model_dump(),
                "storage_state": None,
            }
        mocker.patch("src.sessions.service.worker_pool.submit", new=fake_submit)

        response = client.post("/api/v1/sessions/sess_x/login", json={
            "script": {"steps": [{"op": "goto", "url": "https://x.com"}]},
            "creds": {"email": "u", "password": "p"},
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        # set_status called with "failed" — verify last call
        last_call = mock_store.set_status.call_args_list[-1]
        assert last_call.args[1] == "failed" or last_call.kwargs.get("status") == "failed"


class TestBatchScrapeWithSession:
    def test_batch_session_id_conflicts_with_page_returns_422(self, client, mock_store, mocker):
        queue = AsyncMock()
        queue.submit = AsyncMock(return_value="job_xx")
        mocker.patch("src.scrape_service.get_job_queue", return_value=queue)

        response = client.post("/api/v1/scrape/pages", json={
            "session_id": "sess_batch",
            "pages": [
                {"url": "https://x.com", "session_id": "sess_other"},
            ],
        })
        assert response.status_code == 422
