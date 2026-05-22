from __future__ import annotations

import json
import math
from typing import Any


def normalize_storage_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Round-trip Playwright `storage_state()` through a JSON-safe shape.

    Playwright emits `cookies[i].expires` as:
      - -1 for session cookies,
      - a float timestamp,
      - missing entirely for some cookies.

    On the way back into `browser.new_context(storage_state=...)`, Playwright
    strictly expects either a positive float or the key absent — passing `null`
    raises "expected float, got object" on the first navigation. We therefore
    drop the key entirely for the session-cookie / unknown / non-finite cases,
    and coerce floats to ints (downstream code expects int seconds).
    """
    if state is None:
        return None

    cookies = state.get("cookies") or []
    normalized_cookies: list[dict[str, Any]] = []
    for cookie in cookies:
        cookie_dict = dict(cookie)
        if "expires" in cookie_dict:
            expires = cookie_dict["expires"]
            if expires is None or (isinstance(expires, (int, float)) and expires < 0) or (
                isinstance(expires, float) and not math.isfinite(expires)
            ):
                del cookie_dict["expires"]
            elif isinstance(expires, float):
                cookie_dict["expires"] = int(expires)
        normalized_cookies.append(cookie_dict)

    return {
        "cookies": normalized_cookies,
        "origins": state.get("origins") or [],
    }


def storage_state_bytes(state: dict[str, Any] | None) -> int:
    """Serialized byte-size of the storage state. Used for the per-session cap."""
    if state is None:
        return 0
    return len(json.dumps(state, separators=(",", ":")).encode("utf-8"))
