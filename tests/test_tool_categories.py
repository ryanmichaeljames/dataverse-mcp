"""Tests for DATAVERSE_TOOLS category gating via env-combination subprocess matrix.

Strategy: each scenario is driven in a subprocess that sets the env vars, imports
``dataverse_mcp.server``, and prints the sorted registered tool names (one per line)
to stdout.  The parent process asserts on the printed list.

This avoids the FastMCP singleton / importlib.reload fragility: the MCP ``mcp``
object registers tools at module-import time, so each scenario needs a fresh
interpreter process.

Acceptance criteria covered:
1. No env vars set (DATAVERSE_ALLOW_WRITE/DELETE both absent): default read-only
   tools across all categories register (66 tools).
2. All allow flags set, DATAVERSE_TOOLS unset: all 148 tools register.
3. DATAVERSE_TOOLS=security + both allow flags: only 16 core + 12 security = 28.
4. DATAVERSE_TOOLS=core,solutions + both allow flags: 16 core + 17 solutions = 33.
5. core is always on: DATAVERSE_TOOLS=security (no explicit core) still yields
   core tools in the registered set.
6. Composition: DATAVERSE_TOOLS=security, no allow flags → 10 read core + 7 read
   security = 17 tools.
7. Unknown category: DATAVERSE_TOOLS=bogus,security + both allow flags → warning
   logged, bogus ignored, security+core still register (28 tools).
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_HELPER_SCRIPT = textwrap.dedent("""
    import json
    import sys
    import asyncio
    import dataverse_mcp.server  # noqa: F401  triggers all @tool registrations
    from dataverse_mcp._app import mcp

    async def _list():
        tools = await mcp.list_tools()
        names = sorted(t.name for t in tools)
        print(json.dumps(names))

    asyncio.run(_list())
""")


def _run_scenario(env_overrides: dict) -> list[str]:
    """Run the helper script in a subprocess with the given env vars.

    Returns the sorted list of registered tool names.
    """
    env = {**os.environ, **env_overrides}
    # Ensure the allow flags are absent unless explicitly set
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
    return json.loads(result.stdout.strip())


# ---------------------------------------------------------------------------
# Expected tool sets (derived from decorator analysis)
# ---------------------------------------------------------------------------

# Core tools: environments (5) + tables (11) = 16 total (5 read + 4 write + 2 delete for tables)
_CORE_READ_TOOLS = {
    "dataverse_list_environments",
    "dataverse_whoami",
    "dataverse_get_entity_sets",
    "dataverse_retrieve_user_privileges",
    "dataverse_retrieve_principal_access",
    # tables read
    "dataverse_query_table",
    "dataverse_get_record",
    "dataverse_count_records",
    "dataverse_aggregate_table",
    "dataverse_execute_batch",
}

_CORE_WRITE_TOOLS = {
    "dataverse_create_record",
    "dataverse_update_record",
    "dataverse_associate_records",
    "dataverse_merge_records",
}

_CORE_DELETE_TOOLS = {
    "dataverse_delete_record",
    "dataverse_disassociate_records",
}

_CORE_ALL_TOOLS = _CORE_READ_TOOLS | _CORE_WRITE_TOOLS | _CORE_DELETE_TOOLS

# Security tools: 7 read + 5 write = 12 total
_SECURITY_READ_TOOLS = {
    "dataverse_list_security_roles",
    "dataverse_get_security_role",
    "dataverse_list_teams",
    "dataverse_get_team",
    "dataverse_list_users",
    "dataverse_get_user",
    "dataverse_list_business_units",
}

_SECURITY_WRITE_TOOLS = {
    "dataverse_assign_security_role",
    "dataverse_remove_security_role",
    "dataverse_add_team_members",
    "dataverse_remove_team_members",
    "dataverse_set_user_state",
}

_SECURITY_ALL_TOOLS = _SECURITY_READ_TOOLS | _SECURITY_WRITE_TOOLS

# Solutions tools (solutions category only, not flows): 8 read + 8 write + 1 delete = 17
_SOLUTIONS_READ_TOOLS = {
    "dataverse_list_solutions",
    "dataverse_get_solution",
    "dataverse_list_solution_components",
    "dataverse_get_solution_history",
    "dataverse_list_solution_histories",
    # ALM tools (issue #91)
    "dataverse_export_solution",
    "dataverse_get_import_job",
    "dataverse_list_import_jobs",
}

_SOLUTIONS_WRITE_TOOLS = {
    "dataverse_create_publisher",
    "dataverse_update_publisher",
    "dataverse_create_solution",
    "dataverse_update_solution",
    "dataverse_update_solution_version",
    "dataverse_add_component_to_solution",
    # ALM tools (issue #91)
    "dataverse_import_solution",
    "dataverse_clone_solution_as_patch",
}

_SOLUTIONS_DELETE_TOOLS = {
    "dataverse_remove_component_from_solution",
}

_SOLUTIONS_ALL_TOOLS = _SOLUTIONS_READ_TOOLS | _SOLUTIONS_WRITE_TOOLS | _SOLUTIONS_DELETE_TOOLS

# Flows tools: 1 read + 4 write = 5
_FLOWS_READ_TOOLS = {
    "dataverse_get_cloud_flows",
}

_FLOWS_WRITE_TOOLS = {
    "dataverse_enable_cloud_flow",
    "dataverse_disable_cloud_flow",
    "dataverse_batch_enable_cloud_flows",
    "dataverse_batch_disable_cloud_flows",
}

_FLOWS_ALL_TOOLS = _FLOWS_READ_TOOLS | _FLOWS_WRITE_TOOLS


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def test_default_no_env_vars():
    """No env vars set: only read-only tools register across all categories (66 tools)."""
    tools = _run_scenario({})
    tool_set = set(tools)

    # Write/delete tools must NOT be present
    all_write_delete = (
        _CORE_WRITE_TOOLS | _CORE_DELETE_TOOLS
        | _SECURITY_WRITE_TOOLS
        | _SOLUTIONS_WRITE_TOOLS | _SOLUTIONS_DELETE_TOOLS
        | _FLOWS_WRITE_TOOLS
    )
    assert not (tool_set & all_write_delete), (
        f"Write/delete tools unexpectedly registered: {tool_set & all_write_delete}"
    )

    # All read-only core tools must be present
    assert _CORE_READ_TOOLS <= tool_set, (
        f"Missing core read tools: {_CORE_READ_TOOLS - tool_set}"
    )

    # Total should be 66 (63 + 3 new ALM read tools)
    assert len(tools) == 66, f"Expected 66 default tools, got {len(tools)}: {tools}"


def test_all_categories_all_flags():
    """DATAVERSE_TOOLS unset + both allow flags: all 148 tools register."""
    tools = _run_scenario({
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    assert len(tools) == 148, f"Expected 148 tools, got {len(tools)}"


def test_security_only_with_all_flags():
    """DATAVERSE_TOOLS=security + both allow flags: 16 core + 12 security = 28."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    tool_set = set(tools)

    assert tool_set == _CORE_ALL_TOOLS | _SECURITY_ALL_TOOLS, (
        f"Unexpected tools. Extra: {tool_set - (_CORE_ALL_TOOLS | _SECURITY_ALL_TOOLS)}, "
        f"Missing: {(_CORE_ALL_TOOLS | _SECURITY_ALL_TOOLS) - tool_set}"
    )
    assert len(tools) == 28, f"Expected 28 tools, got {len(tools)}"


def test_core_solutions_with_all_flags():
    """DATAVERSE_TOOLS=core,solutions + both allow flags: 16 core + 17 solutions = 33.

    Flows (a separate category) must NOT register.
    """
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "core,solutions",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    tool_set = set(tools)

    expected = _CORE_ALL_TOOLS | _SOLUTIONS_ALL_TOOLS
    assert tool_set == expected, (
        f"Unexpected tools. Extra: {tool_set - expected}, "
        f"Missing: {expected - tool_set}"
    )
    assert len(tools) == 33, f"Expected 33 tools, got {len(tools)}"

    # Flow tools must not be present
    assert not (tool_set & _FLOWS_ALL_TOOLS), (
        f"Flow tools should not register: {tool_set & _FLOWS_ALL_TOOLS}"
    )


def test_core_always_on_when_omitted():
    """core is always registered even when not listed in DATAVERSE_TOOLS."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    tool_set = set(tools)
    assert _CORE_ALL_TOOLS <= tool_set, (
        f"Core tools missing: {_CORE_ALL_TOOLS - tool_set}"
    )


def test_composition_security_no_allow_flags():
    """DATAVERSE_TOOLS=security, no allow flags: 10 read core + 7 read security = 17."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
    })
    tool_set = set(tools)

    expected = _CORE_READ_TOOLS | _SECURITY_READ_TOOLS
    assert tool_set == expected, (
        f"Unexpected tools. Extra: {tool_set - expected}, "
        f"Missing: {expected - tool_set}"
    )
    assert len(tools) == 17, f"Expected 17 tools, got {len(tools)}"


def test_unknown_category_ignored():
    """Unknown category 'bogus' is ignored; security+core still register (28 tools)."""
    result = subprocess.run(
        [sys.executable, "-c", _HELPER_SCRIPT],
        capture_output=True,
        text=True,
        env={
            **{k: v for k, v in os.environ.items()
               if k not in ("DATAVERSE_ALLOW_WRITE", "DATAVERSE_ALLOW_DELETE", "DATAVERSE_TOOLS")},
            "DATAVERSE_TOOLS": "bogus,security",
            "DATAVERSE_ALLOW_WRITE": "true",
            "DATAVERSE_ALLOW_DELETE": "true",
        },
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    tools = json.loads(result.stdout.strip())
    tool_set = set(tools)

    # Warning about 'bogus' should appear in stderr
    assert "bogus" in result.stderr, (
        "Expected a warning about unknown category 'bogus' in stderr"
    )

    # Security + core tools still register
    assert tool_set == _CORE_ALL_TOOLS | _SECURITY_ALL_TOOLS, (
        f"Extra: {tool_set - (_CORE_ALL_TOOLS | _SECURITY_ALL_TOOLS)}, "
        f"Missing: {(_CORE_ALL_TOOLS | _SECURITY_ALL_TOOLS) - tool_set}"
    )
    assert len(tools) == 28, f"Expected 28 tools, got {len(tools)}"
