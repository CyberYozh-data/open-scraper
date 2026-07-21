"""End-to-end test for Session Phase 1 — real Playwright against an in-test target.

Skipped when Playwright is not available locally. CI runs Playwright already
(per Dockerfile / current e2e test pattern).
"""
from __future__ import annotations

import asyncio
import socket
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Form, Request, Response
from pydantic import SecretStr

from src.app import create_app
from src.settings import settings

# The /sessions surface is gated behind SERVICE_TOKEN (CRIT-02). This test
# drives a real uvicorn instance rather than the shared TestClient fixture, so
# it configures the token itself and presents it on every call.
_SERVICE_TOKEN = "test-e2e-svc-token"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _make_target_app() -> FastAPI:
    """A tiny target site that requires cookie sid=abc."""
    app = FastAPI()

    @app.get("/login")
    def login_form():
        return Response("""
            <html><body>
              <form method=post action=/login>
                <input id=u name=user>
                <input id=p name=passwd>
                <button id=submit type=submit>Go</button>
              </form>
            </body></html>
        """, media_type="text/html")

    @app.post("/login")
    def login_submit(user: str = Form(...), passwd: str = Form(...)):
        if user == "tom" and passwd == "x":
            resp = Response(
                "<html><body><div class=ok>welcome</div></body></html>",
                media_type="text/html",
            )
            resp.set_cookie("sid", "abc", path="/")
            return resp
        return Response("nope", status_code=403)

    @app.get("/secure")
    def secure(request: Request):
        if request.cookies.get("sid") == "abc":
            return Response(
                "<html><body>Welcome to the Secure Area</body></html>",
                media_type="text/html",
            )
        return Response("403 forbidden", status_code=403)

    return app


def _playwright_available() -> bool:
    # The runner imports both playwright and playwright_stealth at module load;
    # if either is missing the worker subprocess crashes before the login DSL runs.
    try:
        import playwright  # type: ignore  # noqa: F401
        import playwright_stealth  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return True


async def _setup_inmemory_broker_state() -> None:
    """Register a real PlaywrightRunner in the InMemoryBroker's state so that
    login_task (which runs immediately in-process via InMemoryBroker) can access
    context.state.runners[engine]. Mirrors what worker.py's WORKER_STARTUP hook
    does in production; needed because the API process's broker.startup() does
    NOT fire WORKER_STARTUP for InMemoryBroker."""
    from src.browser.runner import PlaywrightRunner
    from src.queue.broker import broker
    from src.settings import settings

    runner = PlaywrightRunner(
        headless=settings.headless,
        block_assets=settings.block_assets,
        timeout_ms=settings.request_timeout_ms,
    )
    broker.state.runners = {"chromium": runner}
    broker.state.browser_lock = asyncio.Lock()
    broker.state.pages_since_launch = {"chromium": 0}
    broker.state.last_activity = {"chromium": asyncio.get_running_loop().time()}


@pytest.mark.skipif(
    not _playwright_available(),
    reason="Playwright not installed locally",
)
@pytest.mark.asyncio
async def test_session_login_and_authenticated_scrape(monkeypatch):
    monkeypatch.setattr(settings, "service_token", SecretStr(_SERVICE_TOKEN))

    target_port = _free_port()
    scraper_port = _free_port()

    target_config = uvicorn.Config(
        _make_target_app(), host="127.0.0.1", port=target_port, log_level="error",
    )
    target_server = uvicorn.Server(target_config)
    target_task = asyncio.create_task(target_server.serve())

    scraper_app = create_app()
    scraper_config = uvicorn.Config(
        scraper_app, host="127.0.0.1", port=scraper_port, log_level="error",
    )
    scraper_server = uvicorn.Server(scraper_config)
    scraper_task = asyncio.create_task(scraper_server.serve())

    # Wait for both up
    deadline = time.time() + 60
    async with httpx.AsyncClient() as probe:
        while time.time() < deadline:
            try:
                target_response = await probe.get(f"http://127.0.0.1:{target_port}/login")
                health_response = await probe.get(f"http://127.0.0.1:{scraper_port}/api/v1/health")
                if target_response.status_code == 200 and health_response.status_code == 200:
                    break
            except Exception:
                await asyncio.sleep(0.5)
        else:
            pytest.fail("services did not start in time")

    # Initialise the InMemoryBroker's state with a real runner so that the
    # login_task (executed in-process by InMemoryBroker) can access the browser.
    await _setup_inmemory_broker_state()

    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{scraper_port}/api/v1",
            timeout=120,
            headers={"X-Service-Token": _SERVICE_TOKEN},
        ) as api_client:
            response = await api_client.post("/sessions", json={"ttl_seconds": 600})
            response.raise_for_status()
            session_id = response.json()["session_id"]

            response = await api_client.post(f"/sessions/{session_id}/login", json={
                "script": {
                    "steps": [
                        {"op": "goto", "url": f"http://127.0.0.1:{target_port}/login"},
                        {"op": "fill", "selector": "#u", "value": "$creds_email"},
                        {"op": "fill", "selector": "#p", "value": "$creds_password"},
                        {"op": "click", "selector": "#submit"},
                        {"op": "wait_for_selector", "selector": ".ok"},
                    ],
                    "success_selector": ".ok",
                },
                "creds": {"email": "tom", "password": "x"},
            })
            response.raise_for_status()
            assert response.json()["ok"] is True, response.json()

            response = await api_client.post("/scrape/page", json={
                "url": f"http://127.0.0.1:{target_port}/secure",
                "session_id": session_id,
                "raw_html": True,
            })
            response.raise_for_status()
            job_id = response.json()["job_id"]

            for _ in range(60):
                await asyncio.sleep(0.5)
                st = (await api_client.get(f"/scrape/{job_id}")).json()
                if st["status"] in ("done", "failed", "cancelled"):
                    break
            else:
                pytest.fail(f"scrape did not complete in time: {st}")

            results = (await api_client.get(f"/scrape/{job_id}/results")).json()
            assert results["status"] == "done", results
            body = results["results"][0]["raw_html"]
            assert "Welcome to the Secure Area" in body, body
    finally:
        target_server.should_exit = True
        scraper_server.should_exit = True
        await asyncio.gather(target_task, scraper_task, return_exceptions=True)
