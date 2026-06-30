"""Unit tests for delete safeguard enforcement on security / app tools.

Regression coverage for GH #118: three tools issued HTTP DELETE but were gated
only by @write_tool (DATAVERSE_ALLOW_WRITE).  They must also require
DATAVERSE_ALLOW_DELETE.

Contract now enforced:
- dataverse_remove_security_role  — @delete_tool; requires DATAVERSE_ALLOW_DELETE.
- dataverse_remove_team_members   — @delete_tool; requires DATAVERSE_ALLOW_DELETE.
- dataverse_assign_app_role with action='remove' — keeps @write_tool but adds a
  runtime DATAVERSE_ALLOW_DELETE guard on the remove path. The add path is
  unaffected (works with DATAVERSE_ALLOW_WRITE only).

The @delete_tool gate is a register-time decision (tools are invisible to MCP
clients when ALLOW_DELETE is false).  We verify this via the subprocess approach
used in test_tool_categories.py — each scenario runs in a fresh interpreter so
the module-level constants are evaluated with the correct env.

The assign_app_role runtime guard is tested as a direct function call (like
test_batch_safeguard.py) using monkeypatch to control os.environ at runtime.
"""

import json
import os
import subprocess
import sys
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import AssignAppRoleInput
from dataverse_mcp.tools.apps import dataverse_assign_app_role

_BASE_URL = "https://yourorg.crm.dynamics.com"
_APP_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_ROLE_ID = "11111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> MagicMock:
    """Return a mock FastMCP Context backed by a minimal AppContext."""
    app_ctx = AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


_HELPER_SCRIPT = textwrap.dedent("""
    import json, asyncio, dataverse_mcp.server  # noqa: F401
    from dataverse_mcp._app import mcp

    async def _list():
        tools = await mcp.list_tools()
        names = sorted(t.name for t in tools)
        print(json.dumps(names))

    asyncio.run(_list())
""")


def _run_scenario(env_overrides: dict) -> set[str]:
    """Run the helper in a subprocess and return the registered tool name set."""
    env = {**os.environ, **env_overrides}
    for key in ("DATAVERSE_ALLOW_WRITE", "DATAVERSE_ALLOW_DELETE", "DATAVERSE_TOOLS"):
        if key not in env_overrides:
            env.pop(key, None)

    result = subprocess.run(
        [sys.executable, "-c", _HELPER_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Subprocess failed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return set(json.loads(result.stdout.strip()))


# ---------------------------------------------------------------------------
# @delete_tool gating: tools are absent from MCP when ALLOW_DELETE is false
# ---------------------------------------------------------------------------


def test_remove_security_role_absent_when_delete_disabled():
    """dataverse_remove_security_role must not register when ALLOW_DELETE is false."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
        "DATAVERSE_ALLOW_WRITE": "true",
        # ALLOW_DELETE intentionally absent
    })
    assert "dataverse_remove_security_role" not in tools, (
        "dataverse_remove_security_role must not be registered without DATAVERSE_ALLOW_DELETE=true"
    )


def test_remove_team_members_absent_when_delete_disabled():
    """dataverse_remove_team_members must not register when ALLOW_DELETE is false."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
        "DATAVERSE_ALLOW_WRITE": "true",
        # ALLOW_DELETE intentionally absent
    })
    assert "dataverse_remove_team_members" not in tools, (
        "dataverse_remove_team_members must not be registered without DATAVERSE_ALLOW_DELETE=true"
    )


def test_remove_security_role_present_when_delete_enabled():
    """dataverse_remove_security_role must register when both write and delete are enabled."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    assert "dataverse_remove_security_role" in tools, (
        "dataverse_remove_security_role must register when DATAVERSE_ALLOW_DELETE=true"
    )


def test_remove_team_members_present_when_delete_enabled():
    """dataverse_remove_team_members must register when both write and delete are enabled."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    assert "dataverse_remove_team_members" in tools, (
        "dataverse_remove_team_members must register when DATAVERSE_ALLOW_DELETE=true"
    )


# ---------------------------------------------------------------------------
# assign_app_role: runtime delete guard on the remove path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_app_role_remove_blocked_when_delete_disabled(monkeypatch) -> None:
    """action='remove' must be rejected when ALLOW_DELETE is off, even with ALLOW_WRITE on."""
    monkeypatch.setenv("DATAVERSE_ALLOW_WRITE", "true")
    monkeypatch.delenv("DATAVERSE_ALLOW_DELETE", raising=False)

    params = AssignAppRoleInput(
        dataverse_url=_BASE_URL, app_id=_APP_ID, role_id=_ROLE_ID, action="remove"
    )
    result = json.loads(await dataverse_assign_app_role(params, _make_ctx()))

    assert result["error"] is True
    assert "DATAVERSE_ALLOW_DELETE" in result["message"]


@pytest.mark.asyncio
async def test_assign_app_role_remove_blocked_when_delete_explicitly_false(monkeypatch) -> None:
    """ALLOW_DELETE=false must block action='remove' regardless of write flag."""
    monkeypatch.setenv("DATAVERSE_ALLOW_WRITE", "true")
    monkeypatch.setenv("DATAVERSE_ALLOW_DELETE", "false")

    params = AssignAppRoleInput(
        dataverse_url=_BASE_URL, app_id=_APP_ID, role_id=_ROLE_ID, action="remove"
    )
    result = json.loads(await dataverse_assign_app_role(params, _make_ctx()))

    assert result["error"] is True
    assert "DATAVERSE_ALLOW_DELETE" in result["message"]


@pytest.mark.asyncio
async def test_assign_app_role_add_not_blocked_by_delete_flag(monkeypatch) -> None:
    """action='add' (POST) must not be blocked by the delete safeguard."""
    monkeypatch.setenv("DATAVERSE_ALLOW_WRITE", "true")
    monkeypatch.delenv("DATAVERSE_ALLOW_DELETE", raising=False)

    params = AssignAppRoleInput(
        dataverse_url=_BASE_URL, app_id=_APP_ID, role_id=_ROLE_ID, action="add"
    )

    with patch(
        "dataverse_mcp.tools.apps.build_headers", new=AsyncMock(return_value={})
    ), patch(
        "dataverse_mcp.tools.apps.request_with_retry", new=AsyncMock()
    ) as mock_req:
        resp = MagicMock()
        resp.status_code = 204
        resp.raise_for_status = MagicMock()
        mock_req.return_value = resp

        result = json.loads(await dataverse_assign_app_role(params, _make_ctx()))

    assert result.get("error") is not True
    assert result.get("success") is True
    assert result.get("action") == "add"
    assert mock_req.await_count == 1


@pytest.mark.asyncio
async def test_assign_app_role_remove_allowed_when_delete_enabled(monkeypatch) -> None:
    """action='remove' proceeds to DELETE when both ALLOW_WRITE and ALLOW_DELETE are true."""
    monkeypatch.setenv("DATAVERSE_ALLOW_WRITE", "true")
    monkeypatch.setenv("DATAVERSE_ALLOW_DELETE", "true")

    params = AssignAppRoleInput(
        dataverse_url=_BASE_URL, app_id=_APP_ID, role_id=_ROLE_ID, action="remove"
    )

    with patch(
        "dataverse_mcp.tools.apps.build_headers", new=AsyncMock(return_value={})
    ), patch(
        "dataverse_mcp.tools.apps.request_with_retry", new=AsyncMock()
    ) as mock_req:
        resp = MagicMock()
        resp.status_code = 204
        resp.raise_for_status = MagicMock()
        mock_req.return_value = resp

        result = json.loads(await dataverse_assign_app_role(params, _make_ctx()))

    assert result.get("error") is not True
    assert result.get("success") is True
    assert result.get("action") == "remove"
    assert mock_req.await_count == 1
