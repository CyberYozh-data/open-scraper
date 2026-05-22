"""End-to-end demo: login & session persistence against the-internet.herokuapp.com.

Prerequisite: `docker compose up --build` (scraper on :8000).

Run: `python examples/login_session_scraping.py`
"""
from __future__ import annotations

import sys
import time

import httpx


SCRAPER = "http://localhost:8000/api/v1"


def main() -> None:
    with httpx.Client(base_url=SCRAPER, timeout=120.0) as client:
        # 1. Create the session
        response = client.post("/sessions", json={
            "device": "desktop",
            "proxy_type": "none",
            "ttl_seconds": 3600,
        })
        response.raise_for_status()
        session_id = response.json()["session_id"]
        print(f"created session {session_id}")

        # 2. Run the login script
        response = client.post(f"/sessions/{session_id}/login", json={
            "script": {
                "steps": [
                    {"op": "goto", "url": "https://the-internet.herokuapp.com/login"},
                    {"op": "fill", "selector": "#username", "value": "$creds_email"},
                    {"op": "fill", "selector": "#password", "value": "$creds_password"},
                    {"op": "click", "selector": "button[type=submit]"},
                    {"op": "wait_for_selector", "selector": ".flash.success"},
                ],
                "success_selector": ".flash.success",
            },
            "creds": {"email": "tomsmith", "password": "SuperSecretPassword!"},
        })
        response.raise_for_status()
        result = response.json()
        if not result["ok"]:
            print(f"login failed: {result.get('error')}")
            print(f"failed step: {result.get('failed_step')}")
            sys.exit(1)
        print(f"login ok in {result['took_ms']}ms")

        # 3. Scrape an authenticated page
        response = client.post("/scrape/page", json={
            "url": "https://the-internet.herokuapp.com/secure",
            "session_id": session_id,
            "raw_html": True,
        })
        response.raise_for_status()
        job_id = response.json()["job_id"]

        # 4. Poll for results
        for _ in range(60):
            time.sleep(1)
            job_status = client.get(f"/scrape/{job_id}").json()
            if job_status["status"] in ("done", "failed", "cancelled"):
                break
        else:
            print("polling timed out")
            sys.exit(1)

        results = client.get(f"/scrape/{job_id}/results").json()
        body = results["results"][0]["raw_html"]
        if "Welcome to the Secure Area" in body:
            print("authenticated scrape succeeded")
        else:
            print("secure-area text missing -- session did not carry through")
            sys.exit(1)

        # 5. Clean up
        client.delete(f"/sessions/{session_id}")
        print("session deleted")


if __name__ == "__main__":
    main()
