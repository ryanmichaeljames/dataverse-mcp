"""Live integration coverage for dataverse_get_organization_info.

Two tests:

* ``test_organization_functions_return_200_and_report_shape`` calls the three
  unbound functions directly and *records the observed property names* of each
  response. The Microsoft Learn reference pages name only the response types
  (``RetrieveVersionResponse``, ``RetrieveCurrentOrganizationResponse``,
  ``RetrieveOrganizationInfoResponse``), so the real property names are
  unverified — this test is how they get confirmed. Property names and value
  *types* are printed; values are not, so no org data leaks into the transcript.
  Run with ``-s`` (or ``--capture=no``) to see the report on a passing run.
* ``test_get_organization_info_tool_returns_merged_payload`` drives the real
  tool end to end, asserts the expected top-level keys, and checks that the
  default response summarizes the installed-solution list to ``solutions_count``
  instead of returning the whole array.

Requires (else auto-skipped by tests/integration/conftest.py):
  DATAVERSE_INTEGRATION_URL   — base org URL
  DATAVERSE_INTEGRATION_TOKEN — bearer access token for that org

Read-only — no write/delete env flags needed.
"""

import json
import os
import time
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    AppContext,
    resolve_base_url,
)
from dataverse_mcp.models import GetOrganizationInfoInput
from dataverse_mcp.tools.environments import dataverse_get_organization_info

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_ACCESS_TYPE_LITERAL = "Microsoft.Dynamics.CRM.EndpointAccessType'Default'"


def _url() -> str:
    return os.environ[_INTEGRATION_URL_VAR]


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ[_INTEGRATION_TOKEN_VAR]}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }


def _function_urls(base_url: str) -> dict[str, str]:
    """Return the exact URLs the tool builds, keyed by function name."""
    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
    return {
        "RetrieveVersion": f"{api_root}/RetrieveVersion()",
        "RetrieveCurrentOrganization": (
            f"{api_root}/RetrieveCurrentOrganization(AccessType={_ACCESS_TYPE_LITERAL})"
        ),
        "RetrieveOrganizationInfo": f"{api_root}/RetrieveOrganizationInfo()",
    }


def _describe(value: Any, depth: int = 2) -> Any:
    """Describe a payload's shape — property names and value types, never values."""
    if isinstance(value, dict):
        if depth <= 0:
            return f"dict({len(value)} keys)"
        return {k: _describe(v, depth - 1) for k, v in value.items()}
    if isinstance(value, list):
        if depth <= 0 or not value:
            return f"list[{len(value)}]"
        return [f"list[{len(value)}] of", _describe(value[0], depth - 1)]
    return type(value).__name__


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """MCPServer-style ctx backed by an AppContext pre-seeded with the sandbox token."""
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_organization_functions_return_200_and_report_shape() -> None:
    """Call all three unbound functions and record their real response shapes."""
    base_url = resolve_base_url(_url())
    urls = _function_urls(base_url)
    observed: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for function_name, url in urls.items():
            response = await client.get(url, headers=_headers())
            entry: dict[str, Any] = {"status": response.status_code}
            if response.status_code == 200:
                body = response.json()
                entry["top_level_keys"] = sorted(body.keys())
                entry["shape"] = _describe(body)
            else:
                entry["body"] = response.text[:500]
            observed[function_name] = entry

    print("\n=== Observed response shapes (property names + value types) ===")
    print(json.dumps(observed, indent=2, sort_keys=True))

    # RetrieveVersion and RetrieveCurrentOrganization are available to any
    # authenticated caller; a non-200 here is a real failure.
    for function_name in ("RetrieveVersion", "RetrieveCurrentOrganization"):
        assert observed[function_name]["status"] == 200, (
            f"{function_name} returned HTTP {observed[function_name]['status']}: "
            f"{observed[function_name].get('body')}"
        )
        assert observed[function_name]["top_level_keys"], (
            f"{function_name} returned an empty payload"
        )

    # RetrieveOrganizationInfo may be unavailable or privilege-gated on some orgs —
    # that is exactly the case the tool degrades to partial_errors for, so its
    # status is reported rather than asserted. The observed status is captured
    # above for the record.
    assert "RetrieveOrganizationInfo" in observed


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_get_organization_info_tool_returns_merged_payload() -> None:
    """The tool returns a merged, non-error payload against a live org."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        ctx = _make_live_ctx(client)
        result = json.loads(
            await dataverse_get_organization_info(
                GetOrganizationInfoInput(dataverse_url=_url()), ctx
            )
        )

    print("\n=== dataverse_get_organization_info result shape ===")
    print(json.dumps(_describe(result, depth=3), indent=2, sort_keys=True))
    print(f"partial_errors: {json.dumps(result.get('partial_errors'))}")

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["access_type"] == "Default"
    assert isinstance(result.get("partial_errors"), list)
    assert "version" in result, (
        "convenience 'version' field was not derivable from the RetrieveVersion "
        f"payload; observed: {result.get('retrieve_version')}"
    )
    assert isinstance(result["version"], str) and result["version"]
    assert "current_organization" in result, (
        f"RetrieveCurrentOrganization did not return data: {result.get('partial_errors')}"
    )

    # The default response must stay a small fingerprint: the installed-solution
    # list (several hundred entries on a real org) is summarized to a count.
    org_info = result.get("organization_info")
    if org_info is None:
        pytest.skip(
            "RetrieveOrganizationInfo did not return data on this org: "
            f"{result.get('partial_errors')}"
        )
    container = org_info.get("organizationInfo")
    assert isinstance(container, dict), (
        f"unexpected organization_info shape: {_describe(org_info, depth=2)}"
    )
    assert "Solutions" not in container, (
        "the full Solutions array must not be returned unless include_solutions=True"
    )
    assert isinstance(container.get("solutions_count"), int), (
        f"solutions_count missing from organization_info: {sorted(container)}"
    )
