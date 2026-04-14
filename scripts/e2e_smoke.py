from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx


def fail(msg: str) -> None:
    print(f"[e2e] ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def get_nested(data: dict[str, Any], path: str) -> Any:
    """
    Minimal JSON-path accessor: "a.b.c"
    """
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        fail(msg)


def wait_health(client: httpx.Client, base: str, timeout_s: int = 60) -> None:
    url = f"{base}/api/v1/health"
    print(f"[e2e] waiting for {url} ...")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            response = client.get(url)
            if response.status_code == 200 and response.json().get("status") == "ok":
                print("[e2e] health OK")
                return
        except Exception:
            pass
        time.sleep(1)
    fail(f"service not healthy after {timeout_s}s")


def post_json(client: httpx.Client, base: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{base}{path}"
    response = client.post(url, json=payload)
    if response.status_code != 200:
        fail(f"POST {path} status={response.status_code} body={response.text[:500]}")
    try:
        return response.json()
    except Exception:
        fail(f"POST {path} invalid JSON: {response.text[:500]}")
    return {}  # unreachable


def get_json(client: httpx.Client, base: str, path: str) -> dict[str, Any]:
    url = f"{base}{path}"
    response = client.get(url)
    if response.status_code != 200:
        fail(f"GET {path} status={response.status_code} body={response.text[:500]}")
    try:
        return response.json()
    except Exception:
        fail(f"GET {path} invalid JSON: {response.text[:500]}")
    return {}  # unreachable


def wait_job_done(client: httpx.Client, base: str, job_id: str, timeout_s: int = 60) -> dict[str, Any]:
    """
    Wait until job is done or failed. Returns status JSON.
    """
    path = f"/api/v1/scrape/{job_id}"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job_status = get_json(client, base, path)
        if job_status.get("status") in ("done", "failed"):
            return job_status
        time.sleep(0.5)
    fail(f"job not finished after {timeout_s}s job_id={job_id}")
    return {}


def get_first_result(client: httpx.Client, base: str, job_id: str) -> dict[str, Any]:
    response_data = get_json(client, base, f"/api/v1/scrape/{job_id}/results")
    results = response_data.get("results")
    assert_true(isinstance(results, list) and results, "results missing/empty")
    first_result = results[0]
    assert_true(isinstance(first_result, dict), "result[0] must be object")
    return first_result


def check_common(result: dict[str, Any]) -> None:
    request_id = result.get("request_id")
    assert_true(isinstance(request_id, str) and len(request_id) > 5, "missing/invalid request_id")

    took_ms = result.get("took_ms")
    assert_true(isinstance(took_ms, int) and took_ms >= 0, "missing/invalid took_ms")

    meta = result.get("meta")
    assert_true(isinstance(meta, dict), "meta must be an object")

    warnings = result.get("warnings")
    assert_true(isinstance(warnings, list), "warnings must be a list")

    # Fail fast if worker returned a hard error via warnings.
    for warning in warnings:
        if isinstance(warning, str) and warning.startswith("worker_error:"):
            fail(f"worker_error returned: {warning}")


def create_job_page(client: httpx.Client, base: str, payload: dict[str, Any]) -> str:
    response_data = post_json(client, base, "/api/v1/scrape/page", payload)
    job_id = response_data.get("job_id")
    assert_true(isinstance(job_id, str) and job_id, "missing job_id")
    return job_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.getenv("API_BASE", "http://localhost:8000"))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("E2E_TIMEOUT_S", "60")))
    parser.add_argument("--print-responses", action="store_true")
    args = parser.parse_args()

    base = args.base.rstrip("/")

    with httpx.Client(timeout=120) as client:
        wait_health(client, base, timeout_s=args.timeout)

        # 1) raw_html
        print("[e2e] test raw_html")
        job_id_1 = create_job_page(
            client,
            base,
            {
                "url": "https://example.com",
                "proxy_type": "none",
                "raw_html": True,
            },
        )
        status_1 = wait_job_done(client, base, job_id_1, timeout_s=args.timeout)
        assert_true(status_1.get("status") == "done", f"raw_html: job failed: {status_1}")
        result_1 = get_first_result(client, base, job_id_1)
        if args.print_responses:
            print(result_1)
        check_common(result_1)
        assert_true(get_nested(result_1, "meta.status_code") == 200, "raw_html: meta.status_code != 200")
        raw_html = result_1.get("raw_html")
        assert_true(isinstance(raw_html, str) and len(raw_html) > 100, "raw_html: missing/too short")
        print("[e2e] raw_html OK")

        # 2) css extract
        print("[e2e] test css extract")
        job_id_2 = create_job_page(
            client,
            base,
            {
                "url": "https://example.com",
                "proxy_type": "none",
                "extract": {
                    "type": "css",
                    "fields": {
                        "title": {"selector": "h1", "attr": "text", "required": True}
                    },
                },
            },
        )
        status_2 = wait_job_done(client, base, job_id_2, timeout_s=args.timeout)
        assert_true(status_2.get("status") == "done", f"css: job failed: {status_2}")
        result_2 = get_first_result(client, base, job_id_2)
        if args.print_responses:
            print(result_2)
        check_common(result_2)
        assert_true(get_nested(result_2, "meta.status_code") == 200, "css: meta.status_code != 200")
        assert_true(get_nested(result_2, "data.title") == "Example Domain", "css: title != 'Example Domain'")
        print("[e2e] css extract OK")

        # 3) screenshot
        print("[e2e] test screenshot")
        job_id_3 = create_job_page(
            client,
            base,
            {
                "url": "https://example.com",
                "proxy_type": "none",
                "screenshot": True,
            },
        )
        status_3 = wait_job_done(client, base, job_id_3, timeout_s=args.timeout)
        assert_true(status_3.get("status") == "done", f"screenshot: job failed: {status_3}")
        result_3 = get_first_result(client, base, job_id_3)
        if args.print_responses:
            print(result_3)
        check_common(result_3)
        assert_true(get_nested(result_3, "meta.status_code") == 200, "screenshot: meta.status_code != 200")
        screenshot_b64 = result_3.get("screenshot_base64")
        assert_true(
            isinstance(screenshot_b64, str) and len(screenshot_b64) > 1000,
            "screenshot: missing/too small base64"
        )
        print("[e2e] screenshot OK")

        # 4) mobile device
        print("[e2e] test mobile device")
        job_id_4 = create_job_page(
            client,
            base,
            {
                "url": "https://example.com",
                "proxy_type": "none",
                "device": "mobile",
                "extract": {
                    "type": "css",
                    "fields": {
                        "title": {"selector": "h1", "attr": "text", "required": True}
                    },
                },
            },
        )
        status_4 = wait_job_done(client, base, job_id_4, timeout_s=args.timeout)
        assert_true(status_4.get("status") == "done", f"mobile: job failed: {status_4}")
        result_4 = get_first_result(client, base, job_id_4)
        if args.print_responses:
            print(result_4)
        check_common(result_4)
        assert_true(get_nested(result_4, "data.title") == "Example Domain", "mobile: title != 'Example Domain'")
        print("[e2e] mobile OK")

    print("[e2e] ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
