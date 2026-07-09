from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.queue.store import get_job_store
from src.queue.tasks import LOGIN_RESULT_KEY
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
        mocker.patch("src.scrape_service.scrape_page_task.kiq", new=AsyncMock())

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

        import asyncio as _asyncio
        real_lock = _asyncio.Lock()
        mock_store.lock = lambda session_id: real_lock

        # Stub login_task.kiq: the task won't actually run; instead we
        # pre-populate the result key so the polling loop finds it immediately.
        worker_result = {
            "ok": True,
            "login_result": SessionLoginResult(ok=True, took_ms=42).model_dump(),
            "storage_state": {"cookies": [{"name": "sid", "value": "a"}], "origins": []},
        }

        async def fake_kiq(login_id):
            job_store = get_job_store()
            await job_store.client.set(
                LOGIN_RESULT_KEY.format(login_id),
                json.dumps(worker_result),
                ex=600,
            )

        mocker.patch("src.sessions.service.login_task.kiq", new=fake_kiq)

        response = client.post("/api/v1/sessions/sess_x/login", json={
            "script": {"steps": [{"op": "goto", "url": "https://x.com"}]},
            "creds": {"email": "u", "password": "p"},
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["took_ms"] == 42
        mock_store.update_storage_state.assert_awaited_once()

    def test_login_slow_but_successful_is_not_timed_out(self, client, mock_store, mocker):
        """A login that completes just after login_task_timeout_s (worker picked
        it up late or it ran slow) must still be consumed, not 504'd with its
        storage_state lost. The API wait deadline carries a grace margin beyond
        the worker timeout to absorb queue-pickup skew."""
        from src.sessions.models import SessionRecord, SessionLoginResult
        from src.sessions import service as svc

        session_record = SessionRecord(
            session_id="sess_x", status="created",
            created_at=1.0, expires_at=86401.0, last_used_at=1.0,
            device="desktop", proxy_type="none",
        )
        mock_store.get = AsyncMock(return_value=session_record)
        mock_store.set_status = AsyncMock()
        mock_store.update_storage_state = AsyncMock()
        import asyncio as _asyncio
        real_lock = _asyncio.Lock()
        mock_store.lock = lambda session_id: real_lock

        mocker.patch.object(svc.settings, "login_task_timeout_s", 10.0)

        worker_result = {
            "ok": True,
            "login_result": SessionLoginResult(ok=True, took_ms=11000).model_dump(),
            "storage_state": {"cookies": [], "origins": []},
        }
        result_json = json.dumps(worker_result)

        # Virtual clock: each poll sleep advances it; the worker result becomes
        # available at t=12 — past the 10s worker timeout but within the grace.
        vclock = [0.0]
        mocker.patch("src.sessions.service._loop_time", side_effect=lambda: vclock[0])

        async def fake_sleep(seconds):
            vclock[0] += seconds
        mocker.patch("src.sessions.service._poll_sleep", new=fake_sleep)

        async def fake_getdel(key):
            return result_json if vclock[0] >= 12.0 else None
        mocker.patch("src.sessions.service.login_task.kiq", new=AsyncMock())
        mocker.patch.object(get_job_store().client, "set", new=AsyncMock())
        mocker.patch.object(get_job_store().client, "getdel", side_effect=fake_getdel)

        response = client.post("/api/v1/sessions/sess_x/login", json={
            "script": {"steps": [{"op": "goto", "url": "https://x.com"}]},
            "creds": {"email": "u", "password": "p"},
        })
        assert response.status_code == 200
        assert response.json()["ok"] is True
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

        worker_result = {
            "ok": True,
            "login_result": SessionLoginResult(
                ok=False, failed_step_index=1, error="selector not found",
            ).model_dump(),
            "storage_state": None,
        }

        async def fake_kiq(login_id):
            job_store = get_job_store()
            await job_store.client.set(
                LOGIN_RESULT_KEY.format(login_id),
                json.dumps(worker_result),
                ex=600,
            )

        mocker.patch("src.sessions.service.login_task.kiq", new=fake_kiq)

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
        mocker.patch("src.scrape_service.scrape_page_task.kiq", new=AsyncMock())

        response = client.post("/api/v1/scrape/pages", json={
            "session_id": "sess_batch",
            "pages": [
                {"url": "https://x.com", "session_id": "sess_other"},
            ],
        })
        assert response.status_code == 422
