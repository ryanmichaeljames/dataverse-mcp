"""Tests for DATAVERSE_TOOLS category gating via env-combination subprocess matrix.

Strategy: each scenario is driven in a subprocess that sets the env vars, imports
``dataverse_mcp.server``, and prints the sorted registered tool names (one per line)
to stdout.  The parent process asserts on the printed list.

This avoids the MCPServer singleton / importlib.reload fragility: the MCP ``mcp``
object registers tools at module-import time, so each scenario needs a fresh
interpreter process.

Acceptance criteria covered:
1. No env vars set (DATAVERSE_ALLOW_WRITE/DELETE both absent): default read-only
   tools across all categories register (97 tools).
2. All allow flags set, DATAVERSE_TOOLS unset: all 200 tools register.
3. DATAVERSE_TOOLS=security + both allow flags: only 24 core + 22 security = 46.
4. DATAVERSE_TOOLS=core,solutions + both allow flags: 24 core + 21 solutions = 45.
5. core is always on: DATAVERSE_TOOLS=security (no explicit core) still yields
   core tools in the registered set.
6. Composition: DATAVERSE_TOOLS=security, no allow flags → 16 read core + 17 read
   security = 33 tools.
7. Unknown category: DATAVERSE_TOOLS=bogus,security + both allow flags → warning
   logged, bogus ignored, security+core still register (46 tools).
8. DATAVERSE_TOOLS=jobs + both allow flags: 24 core + 3 jobs = 27 tools.
9. DATAVERSE_TOOLS=security (no jobs): jobs tools absent.
10. DATAVERSE_TOOLS=webresources + both allow flags: 24 core + 5 webresources = 29.
11. DATAVERSE_TOOLS=security (no webresources): webresource tools absent.
12. DATAVERSE_TOOLS=customapis + both allow flags: 24 core + 13 customapis = 37.
13. DATAVERSE_TOOLS=security (no customapis): customapi tools absent.
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

# Core tools: environments (7) + tables (16) + customizations (1) = 24 total
# (16 read + 6 write + 2 delete)
_CORE_READ_TOOLS = {
    "dataverse_list_environments",
    "dataverse_whoami",
    "dataverse_get_organization_info",
    "dataverse_get_setting",
    "dataverse_get_entity_sets",
    "dataverse_retrieve_user_privileges",
    "dataverse_retrieve_principal_access",
    # tables read
    "dataverse_query_table",
    "dataverse_execute_fetchxml",
    "dataverse_validate_fetchxml",
    "dataverse_get_record",
    "dataverse_count_records",
    "dataverse_get_total_record_counts",
    "dataverse_aggregate_table",
    "dataverse_execute_batch",
    # customizations read (spans views/forms/sitemaps/apps/webresources, so it
    # lives in the always-on core category rather than any one domain)
    "dataverse_retrieve_unpublished",
}

_CORE_WRITE_TOOLS = {
    "dataverse_create_record",
    "dataverse_update_record",
    "dataverse_swap_flow_connection_reference",
    "dataverse_associate_records",
    "dataverse_merge_records",
    "dataverse_bulk_upsert",
}

_CORE_DELETE_TOOLS = {
    "dataverse_delete_record",
    "dataverse_disassociate_records",
}

_CORE_ALL_TOOLS = _CORE_READ_TOOLS | _CORE_WRITE_TOOLS | _CORE_DELETE_TOOLS

# Security tools: 17 read + 3 write + 2 delete = 22 total
_SECURITY_READ_TOOLS = {
    "dataverse_list_security_roles",
    "dataverse_get_security_role",
    "dataverse_get_role_privileges",
    "dataverse_get_team_privileges",
    # Environment-wide privilege catalogue (privileges / privilegeobjecttypecodesset)
    "dataverse_list_privileges",
    "dataverse_retrieve_access_origin",
    "dataverse_list_shared_principals",
    "dataverse_list_teams",
    "dataverse_get_team",
    "dataverse_list_users",
    "dataverse_get_user",
    "dataverse_list_business_units",
    "dataverse_audit_user_access",
    # Audit history tools (issue #97)
    "dataverse_retrieve_record_change_history",
    "dataverse_get_audit_details",
    "dataverse_list_audit",
    # Column-scoped audit history
    "dataverse_get_attribute_change_history",
}

_SECURITY_WRITE_TOOLS = {
    "dataverse_assign_security_role",
    "dataverse_add_team_members",
    "dataverse_set_user_state",
}

_SECURITY_DELETE_TOOLS = {
    "dataverse_remove_security_role",
    "dataverse_remove_team_members",
}

_SECURITY_ALL_TOOLS = _SECURITY_READ_TOOLS | _SECURITY_WRITE_TOOLS | _SECURITY_DELETE_TOOLS

# Jobs tools: 2 read + 1 write = 3 total
_JOBS_READ_TOOLS = {
    "dataverse_list_async_operations",
    "dataverse_get_async_operation",
}

_JOBS_WRITE_TOOLS = {
    "dataverse_cancel_async_operation",
}

_JOBS_ALL_TOOLS = _JOBS_READ_TOOLS | _JOBS_WRITE_TOOLS

# Web resource tools: 2 read + 2 write + 1 delete = 5 total
_WEBRESOURCES_READ_TOOLS = {
    "dataverse_list_web_resources",
    "dataverse_get_web_resource",
}

_WEBRESOURCES_WRITE_TOOLS = {
    "dataverse_create_web_resource",
    "dataverse_update_web_resource",
}

_WEBRESOURCES_DELETE_TOOLS = {
    "dataverse_delete_web_resource",
}

_WEBRESOURCES_ALL_TOOLS = (
    _WEBRESOURCES_READ_TOOLS | _WEBRESOURCES_WRITE_TOOLS | _WEBRESOURCES_DELETE_TOOLS
)

# Custom APIs tools: 4 read + 6 write + 3 delete = 13 total
_CUSTOMAPIS_READ_TOOLS = {
    "dataverse_list_custom_apis",
    "dataverse_get_custom_api",
    "dataverse_list_custom_api_request_parameters",
    "dataverse_list_custom_api_response_properties",
}

_CUSTOMAPIS_WRITE_TOOLS = {
    "dataverse_create_custom_api",
    "dataverse_update_custom_api",
    "dataverse_create_custom_api_request_parameter",
    "dataverse_update_custom_api_request_parameter",
    "dataverse_create_custom_api_response_property",
    "dataverse_update_custom_api_response_property",
}

_CUSTOMAPIS_DELETE_TOOLS = {
    "dataverse_delete_custom_api",
    "dataverse_delete_custom_api_request_parameter",
    "dataverse_delete_custom_api_response_property",
}

_CUSTOMAPIS_ALL_TOOLS = (
    _CUSTOMAPIS_READ_TOOLS | _CUSTOMAPIS_WRITE_TOOLS | _CUSTOMAPIS_DELETE_TOOLS
)

# Solutions tools (solutions category only, not flows): 10 read + 10 write + 1 delete = 21
_SOLUTIONS_READ_TOOLS = {
    "dataverse_list_solutions",
    "dataverse_get_solution",
    "dataverse_list_solution_components",
    "dataverse_get_solution_history",
    "dataverse_list_solution_histories",
    # ALM tools (issue #91)
    "dataverse_export_solution",
    "dataverse_get_import_job",
    "dataverse_get_import_job_results",
    "dataverse_list_import_jobs",
    # Dependency analysis (issue #104)
    "dataverse_analyze_dependencies",
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
    "dataverse_stage_and_upgrade_solution",
    "dataverse_delete_and_promote_solution",
}

_SOLUTIONS_DELETE_TOOLS = {
    "dataverse_remove_component_from_solution",
}

_SOLUTIONS_ALL_TOOLS = _SOLUTIONS_READ_TOOLS | _SOLUTIONS_WRITE_TOOLS | _SOLUTIONS_DELETE_TOOLS

# Flows tools: 2 read + 6 write = 8
_FLOWS_READ_TOOLS = {
    "dataverse_get_cloud_flows",
    "dataverse_list_processes",
}

_FLOWS_WRITE_TOOLS = {
    "dataverse_enable_cloud_flow",
    "dataverse_disable_cloud_flow",
    "dataverse_batch_enable_cloud_flows",
    "dataverse_batch_disable_cloud_flows",
    "dataverse_activate_process",
    "dataverse_deactivate_process",
}

_FLOWS_ALL_TOOLS = _FLOWS_READ_TOOLS | _FLOWS_WRITE_TOOLS


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def test_default_no_env_vars():
    """No env vars set: only read-only tools register across all categories (97 tools)."""
    tools = _run_scenario({})
    tool_set = set(tools)

    # Write/delete tools must NOT be present
    all_write_delete = (
        _CORE_WRITE_TOOLS | _CORE_DELETE_TOOLS
        | _SECURITY_WRITE_TOOLS | _SECURITY_DELETE_TOOLS
        | _SOLUTIONS_WRITE_TOOLS | _SOLUTIONS_DELETE_TOOLS
        | _FLOWS_WRITE_TOOLS
        | _JOBS_WRITE_TOOLS
        | _WEBRESOURCES_WRITE_TOOLS | _WEBRESOURCES_DELETE_TOOLS
        | _CUSTOMAPIS_WRITE_TOOLS | _CUSTOMAPIS_DELETE_TOOLS
    )
    assert not (tool_set & all_write_delete), (
        f"Write/delete tools unexpectedly registered: {tool_set & all_write_delete}"
    )

    # Alternate key read tool must be present (schema is always-on when DATAVERSE_TOOLS unset)
    assert "dataverse_list_alternate_keys" in tool_set, (
        "dataverse_list_alternate_keys missing from default read-only tool set"
    )
    # Component customizability pre-flight is a read-only schema tool
    assert "dataverse_is_component_customizable" in tool_set, (
        "dataverse_is_component_customizable missing from default read-only tool set"
    )
    # Language and relationship-eligibility enumeration are read-only schema tools
    assert "dataverse_list_languages" in tool_set, (
        "dataverse_list_languages missing from default read-only tool set"
    )
    assert "dataverse_get_valid_relationship_entities" in tool_set, (
        "dataverse_get_valid_relationship_entities missing from default read-only tool set"
    )
    # Alternate key write/delete tools must NOT be present
    assert "dataverse_create_alternate_key" not in tool_set
    assert "dataverse_delete_alternate_key" not in tool_set

    # All read-only core tools must be present
    assert _CORE_READ_TOOLS <= tool_set, (
        f"Missing core read tools: {_CORE_READ_TOOLS - tool_set}"
    )

    # Jobs read tools must be present (jobs is always-on when DATAVERSE_TOOLS unset)
    assert _JOBS_READ_TOOLS <= tool_set, (
        f"Missing jobs read tools: {_JOBS_READ_TOOLS - tool_set}"
    )

    # Webresource read tools must be present (webresources is always-on when DATAVERSE_TOOLS unset)
    assert _WEBRESOURCES_READ_TOOLS <= tool_set, (
        f"Missing webresources read tools: {_WEBRESOURCES_READ_TOOLS - tool_set}"
    )

    # Custom API read tools must be present (customapis is always-on when DATAVERSE_TOOLS unset)
    assert _CUSTOMAPIS_READ_TOOLS <= tool_set, (
        f"Missing customapis read tools: {_CUSTOMAPIS_READ_TOOLS - tool_set}"
    )

    # Classic process list tool must be present (flows is always-on when DATAVERSE_TOOLS unset)
    assert "dataverse_list_processes" in tool_set, (
        "dataverse_list_processes missing from default read-only tool set"
    )
    # Activate/deactivate process tools must NOT be present (gated behind DATAVERSE_ALLOW_WRITE)
    assert "dataverse_activate_process" not in tool_set
    assert "dataverse_deactivate_process" not in tool_set

    # Total should be 97 (96 + dataverse_list_privileges, a new read-only
    # security tool)
    assert len(tools) == 97, f"Expected 97 default tools, got {len(tools)}: {tools}"


def test_all_categories_all_flags():
    """DATAVERSE_TOOLS unset + both allow flags: all 200 tools register."""
    tools = _run_scenario({
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    assert len(tools) == 200, f"Expected 200 tools, got {len(tools)}"


def test_security_only_with_all_flags():
    """DATAVERSE_TOOLS=security + both allow flags: 24 core + 22 security = 46."""
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
    assert len(tools) == 46, f"Expected 46 tools, got {len(tools)}"


def test_core_solutions_with_all_flags():
    """DATAVERSE_TOOLS=core,solutions + both allow flags: 24 core + 21 solutions = 45.

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
    assert len(tools) == 45, f"Expected 45 tools, got {len(tools)}"

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
    """DATAVERSE_TOOLS=security, no allow flags: 16 read core + 17 read security = 33."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
    })
    tool_set = set(tools)

    expected = _CORE_READ_TOOLS | _SECURITY_READ_TOOLS
    assert tool_set == expected, (
        f"Unexpected tools. Extra: {tool_set - expected}, "
        f"Missing: {expected - tool_set}"
    )
    assert len(tools) == 33, f"Expected 33 tools, got {len(tools)}"


def test_unknown_category_ignored():
    """Unknown category 'bogus' is ignored; security+core still register (46 tools)."""
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
    assert len(tools) == 46, f"Expected 46 tools, got {len(tools)}"


def test_jobs_only_with_all_flags():
    """DATAVERSE_TOOLS=jobs + both allow flags: 24 core + 3 jobs = 27."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "jobs",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    tool_set = set(tools)

    expected = _CORE_ALL_TOOLS | _JOBS_ALL_TOOLS
    assert tool_set == expected, (
        f"Unexpected tools. Extra: {tool_set - expected}, "
        f"Missing: {expected - tool_set}"
    )
    assert len(tools) == 27, f"Expected 27 tools, got {len(tools)}"

    # Security tools must not be present
    assert not (tool_set & _SECURITY_ALL_TOOLS), (
        f"Security tools should not register: {tool_set & _SECURITY_ALL_TOOLS}"
    )


def test_security_only_no_jobs():
    """DATAVERSE_TOOLS=security (no jobs): jobs tools absent."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    tool_set = set(tools)

    # Jobs tools must NOT register when jobs category is not requested
    assert not (tool_set & _JOBS_ALL_TOOLS), (
        f"Jobs tools should not register when DATAVERSE_TOOLS=security: "
        f"{tool_set & _JOBS_ALL_TOOLS}"
    )


def test_webresources_only_with_all_flags():
    """DATAVERSE_TOOLS=webresources + both allow flags: 24 core + 5 webresources = 29."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "webresources",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    tool_set = set(tools)

    expected = _CORE_ALL_TOOLS | _WEBRESOURCES_ALL_TOOLS
    assert tool_set == expected, (
        f"Unexpected tools. Extra: {tool_set - expected}, "
        f"Missing: {expected - tool_set}"
    )
    assert len(tools) == 29, f"Expected 29 tools, got {len(tools)}"

    # Security tools must not be present
    assert not (tool_set & _SECURITY_ALL_TOOLS), (
        f"Security tools should not register: {tool_set & _SECURITY_ALL_TOOLS}"
    )


def test_security_only_no_webresources():
    """DATAVERSE_TOOLS=security (no webresources): webresource tools absent."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    tool_set = set(tools)

    # Web resource tools must NOT register when webresources category is not requested
    assert not (tool_set & _WEBRESOURCES_ALL_TOOLS), (
        f"Web resource tools should not register when DATAVERSE_TOOLS=security: "
        f"{tool_set & _WEBRESOURCES_ALL_TOOLS}"
    )


def test_customapis_only_with_all_flags():
    """DATAVERSE_TOOLS=customapis + both allow flags: 24 core + 13 customapis = 37."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "customapis",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    tool_set = set(tools)

    expected = _CORE_ALL_TOOLS | _CUSTOMAPIS_ALL_TOOLS
    assert tool_set == expected, (
        f"Unexpected tools. Extra: {tool_set - expected}, "
        f"Missing: {expected - tool_set}"
    )
    assert len(tools) == 37, f"Expected 37 tools, got {len(tools)}"

    # Security tools must not be present
    assert not (tool_set & _SECURITY_ALL_TOOLS), (
        f"Security tools should not register: {tool_set & _SECURITY_ALL_TOOLS}"
    )


def test_security_only_no_customapis():
    """DATAVERSE_TOOLS=security (no customapis): customapis tools absent."""
    tools = _run_scenario({
        "DATAVERSE_TOOLS": "security",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true",
    })
    tool_set = set(tools)

    # Custom API tools must NOT register when customapis category is not requested
    assert not (tool_set & _CUSTOMAPIS_ALL_TOOLS), (
        f"Custom API tools should not register when DATAVERSE_TOOLS=security: "
        f"{tool_set & _CUSTOMAPIS_ALL_TOOLS}"
    )
