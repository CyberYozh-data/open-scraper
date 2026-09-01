"""What the (unauthenticated) MCP transport advertises as agent tools.

The denylist this replaces could not do its job: `mcp_excluded_operations()`
filtered on `route.operation_id`, and the eight `/api/v2/prem-proxies/*` routes
declare none, so FastAPI generated ids like
`sub_users_api_v2_prem_proxies_sub_users_get` that no hand-written entry could
ever name. They were live tools, anonymously callable, while the docstring
promised nothing could drift onto the surface. Green CI throughout.

A denylist fails by silently exposing; an allowlist fails by a missing tool.
Only one of those is safe to get wrong.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.app as app_mod
from src.app import MCP_TOOLS, create_app


def _build_and_capture() -> tuple[object, set[str]]:
    """Return the app AND the tool names of the MCP instance `create_app`
    itself built.

    Deliberately NOT `FastApiMCP(app, include_operations=list(MCP_TOOLS))`:
    that would construct a second, correct instance and assert about it,
    passing even if `create_app` still shipped the old `exclude_operations`
    call. The wiring is the thing under test, so spy on the real one.
    """
    captured = {}
    real = app_mod.FastApiMCP

    class _Spy(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured["mcp"] = self

    app_mod.FastApiMCP = _Spy
    try:
        app = create_app()
    finally:
        app_mod.FastApiMCP = real
    assert "mcp" in captured, "create_app did not construct an MCP server"
    return app, {tool.name for tool in captured["mcp"].tools}


def _advertised() -> set[str]:
    return _build_and_capture()[1]


def test_advertised_tools_are_exactly_the_allowlist():
    assert _advertised() == set(MCP_TOOLS)


def test_prem_proxy_operations_are_not_advertised():
    """Red at HEAD before this change: all eight were live tools."""
    assert not [name for name in _advertised() if "prem_proxies" in name]


def test_session_ops_and_resolve_proxy_are_not_advertised():
    """Replaces the old test, which recomputed the implementation's own
    comprehension and therefore could not see drift."""
    advertised = _advertised()
    for name in (
        "resolve_proxy",
        "create_session",
        "list_sessions",
        "get_session",
        "delete_session",
        "inject_session_cookies",
        "login_session",
    ):
        assert name not in advertised, name


def test_every_allowlisted_name_exists_in_the_openapi():
    """A typo in MCP_TOOLS must not silently shrink the surface."""
    app = create_app()
    known = {
        operation.get("operationId")
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict)
    }
    missing = sorted(set(MCP_TOOLS) - known)
    assert not missing, f"MCP_TOOLS names operations that do not exist: {missing}"


def test_startup_rejects_an_allowlist_naming_an_unknown_operation(monkeypatch):
    import src.app as app_mod

    monkeypatch.setattr(app_mod, "MCP_TOOLS", ("health", "no_such_operation"))
    with pytest.raises(RuntimeError, match="no_such_operation"):
        app_mod.create_app()


def test_startup_rejects_an_empty_allowlist(monkeypatch):
    """fastapi-mcp prunes `operation_map` only when the filtered list is
    non-empty, so an allowlist matching nothing advertises zero tools while
    leaving every operation callable by name through `tools/call`. That is a
    fail-open, and it must not be reachable by editing one tuple."""
    import src.app as app_mod

    monkeypatch.setattr(app_mod, "MCP_TOOLS", ())
    with pytest.raises(RuntimeError):
        app_mod.create_app()


def test_no_anonymously_gated_operation_is_advertised(monkeypatch):
    """An independent oracle: ask the running app which operations refuse an
    anonymous caller, and assert none of them is an agent tool.

    It does not read MCP_TOOLS to decide what *should* be gated — it probes —
    so it keeps biting after a future endpoint is put behind the service token
    and its author forgets the MCP half.

    The upstream client is stubbed out. Without that, probing the prem-proxy
    routes made FOUR live calls to the paid vendor API on every run: 8.6
    seconds, real account data written into the module cache, and four tokens
    of the very budget this change introduces to protect that quota. CI was
    green only because CI has no API key — and ci.yml states as an invariant
    that no test in the fast gate needs network egress.
    """
    from unittest.mock import patch

    from src.api import prem_proxies

    prem_proxies.reset_relay_state()
    app, advertised = _build_and_capture()
    spec = app.openapi()

    gated: list[str] = []
    with patch("src.api.prem_proxies.proxy_resolver") as resolver:
        # None, not an AsyncMock: every prem route short-circuits to its
        # unconfigured shape, so the probe exercises the AUTH decision without
        # inventing upstream data or touching the vendor.
        resolver._client_v2 = None
        with TestClient(app) as client:
            for path, operations in spec["paths"].items():
                get_op = operations.get("get")
                if not get_op or "{" in path:
                    continue
                operation_id = get_op.get("operationId")
                if not operation_id:
                    continue
                # Required params get a plausible value rather than being
                # skipped: skipping them hid HALF the routes in this very file
                # (the four geo endpoints all require country_code), so the
                # oracle could not see the surface it exists to watch.
                params = {
                    p["name"]: "DE"
                    for p in get_op.get("parameters", []) or []
                    if p.get("required")
                }
                if client.get(path, params=params).status_code in (401, 403, 503):
                    gated.append(operation_id)
    prem_proxies.reset_relay_state()

    leaked = sorted(set(gated) & advertised)
    assert not leaked, f"gated operations advertised as MCP tools: {leaked}"
