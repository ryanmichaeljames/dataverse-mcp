"""Unit tests for dataverse_get_organization_info.

Acceptance criteria:
- Happy path: all three unbound functions succeed; payloads are merged, the
  @odata envelope is stripped, and the convenience `version` field is derived
  without assuming a hard-coded property path.
- Solution summarization: the several-hundred-entry `Solutions` array from
  RetrieveOrganizationInfo is replaced by `solutions_count` by default, returned
  in full only when `include_solutions=True`, and handled without raising (and
  without a bogus count) when absent or not a list.
- Partial failure: one function fails (e.g. privilege-gated
  RetrieveOrganizationInfo); it is recorded in `partial_errors` and the other
  two payloads are still returned as a success response.
- Total failure: all three fail; the standard {"error": true, ...} envelope is
  returned.
- Input validation: access_type outside the three EndpointAccessType members is
  rejected by the input model, so no unvalidated value reaches the URL.

All HTTP is mocked; no network access.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import GetOrganizationInfoInput
from dataverse_mcp.tools.environments import dataverse_get_organization_info

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"

_VERSION_URL = f"{_API_ROOT}/RetrieveVersion()"
_CURRENT_ORG_URL = (
    f"{_API_ROOT}/RetrieveCurrentOrganization"
    "(AccessType=Microsoft.Dynamics.CRM.EndpointAccessType'Default')"
)
_ORG_INFO_URL = f"{_API_ROOT}/RetrieveOrganizationInfo()"

# Representative payloads modelled on the live-verified response shapes. The tool
# passes property names through unchanged, so these fixtures exercise the merge,
# envelope-stripping, solution summarization, and fault-tolerance behaviour.
_VERSION_BODY = {
    "@odata.context": f"{_API_ROOT}/$metadata#Edm.String",
    "Version": "9.2.24073.198",
}
_CURRENT_ORG_BODY = {
    "@odata.context": f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM.RetrieveCurrentOrganizationResponse",
    "Detail": {
        "OrganizationId": "11111111-2222-3333-4444-555555555555",
        "FriendlyName": "Contoso Dev",
        "UniqueName": "unqcontosodev",
        "OrganizationType": "Developer",
        "Endpoints": [{"key": "WebApplication", "value": f"{_BASE_URL}/"}],
    },
}
_SOLUTIONS = [
    {
        "Id": f"aaaaaaaa-0000-0000-0000-00000000000{index}",
        "SolutionUniqueName": f"contososolution{index}",
        "FriendlyName": f"Contoso Solution {index}",
    }
    for index in range(3)
]
_ORG_INFO_BODY = {
    "@odata.context": f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM.RetrieveOrganizationInfoResponse",
    "organizationInfo": {
        "InstanceType": "Developer",
        "SingletonScenarioName": None,
        "Solutions": _SOLUTIONS,
    },
}
# What the tool returns for _ORG_INFO_BODY by default: Solutions traded for a count,
# every other property preserved.
_ORG_INFO_SUMMARIZED = {
    "organizationInfo": {
        "InstanceType": "Developer",
        "SingletonScenarioName": None,
        "solutions_count": 3,
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> MagicMock:
    """Return a mock MCPServer Context backed by a minimal AppContext."""
    app_ctx = AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _ok(url: str, body: dict) -> httpx.Response:
    """Return a real 200 httpx.Response so raise_for_status/json behave normally."""
    return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def _err(url: str, status_code: int, code: str, message: str) -> httpx.Response:
    """Return a real error httpx.Response carrying an OData error body."""
    return httpx.Response(
        status_code,
        json={"error": {"code": code, "message": message}},
        request=httpx.Request("GET", url),
    )


def _router(responses: dict[str, httpx.Response]):
    """Build a request_with_retry side_effect that dispatches on the request URL."""

    async def _side_effect(_client, _method, url, **_kwargs):
        assert url in responses, f"Unexpected URL requested: {url}"
        return responses[url]

    return _side_effect


def _patched(side_effect):
    """Patch build_headers and request_with_retry in the environments module."""
    return (
        patch(
            "dataverse_mcp.tools.environments.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.environments.request_with_retry",
            new=AsyncMock(side_effect=side_effect),
        ),
    )


async def _run(params: GetOrganizationInfoInput, org_info_body: dict) -> dict:
    """Run the tool with all three functions succeeding, varying only the org-info body."""
    responses = {
        _VERSION_URL: _ok(_VERSION_URL, _VERSION_BODY),
        _CURRENT_ORG_URL: _ok(_CURRENT_ORG_URL, _CURRENT_ORG_BODY),
        _ORG_INFO_URL: _ok(_ORG_INFO_URL, org_info_body),
    }
    headers_patch, request_patch = _patched(_router(responses))
    with headers_patch, request_patch:
        return json.loads(await dataverse_get_organization_info(params, _make_ctx()))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_functions_merged() -> None:
    """All three succeed: payloads merged, envelope stripped, version derived."""
    params = GetOrganizationInfoInput(dataverse_url=_BASE_URL)
    responses = {
        _VERSION_URL: _ok(_VERSION_URL, _VERSION_BODY),
        _CURRENT_ORG_URL: _ok(_CURRENT_ORG_URL, _CURRENT_ORG_BODY),
        _ORG_INFO_URL: _ok(_ORG_INFO_URL, _ORG_INFO_BODY),
    }
    headers_patch, request_patch = _patched(_router(responses))

    with headers_patch, request_patch as mock_request:
        result = json.loads(await dataverse_get_organization_info(params, _make_ctx()))

    assert "error" not in result
    assert result["version"] == "9.2.24073.198"
    assert result["access_type"] == "Default"
    assert result["partial_errors"] == []
    # Raw payloads pass through unchanged apart from the @odata envelope and the
    # summarized Solutions array.
    assert result["current_organization"] == {"Detail": _CURRENT_ORG_BODY["Detail"]}
    assert result["organization_info"] == _ORG_INFO_SUMMARIZED
    assert "@odata.context" not in result["retrieve_version"]

    # Exactly the three documented URLs are requested, in order.
    requested = [call.args[2] for call in mock_request.await_args_list]
    assert requested == [_VERSION_URL, _CURRENT_ORG_URL, _ORG_INFO_URL]


@pytest.mark.asyncio
async def test_access_type_is_used_in_the_enum_literal() -> None:
    """A non-default access_type appears in the OData enum literal in the URL."""
    params = GetOrganizationInfoInput(dataverse_url=_BASE_URL, access_type="Internet")
    internet_url = (
        f"{_API_ROOT}/RetrieveCurrentOrganization"
        "(AccessType=Microsoft.Dynamics.CRM.EndpointAccessType'Internet')"
    )
    responses = {
        _VERSION_URL: _ok(_VERSION_URL, _VERSION_BODY),
        internet_url: _ok(internet_url, _CURRENT_ORG_BODY),
        _ORG_INFO_URL: _ok(_ORG_INFO_URL, _ORG_INFO_BODY),
    }
    headers_patch, request_patch = _patched(_router(responses))

    with headers_patch, request_patch as mock_request:
        result = json.loads(await dataverse_get_organization_info(params, _make_ctx()))

    assert result["access_type"] == "Internet"
    assert internet_url in [call.args[2] for call in mock_request.await_args_list]


@pytest.mark.asyncio
async def test_version_found_when_property_is_not_named_version() -> None:
    """The version convenience field is derived from a lone string property."""
    params = GetOrganizationInfoInput(dataverse_url=_BASE_URL)
    body = {"@odata.context": "ctx", "SomeOtherName": "9.2.0.0"}
    responses = {
        _VERSION_URL: _ok(_VERSION_URL, body),
        _CURRENT_ORG_URL: _ok(_CURRENT_ORG_URL, _CURRENT_ORG_BODY),
        _ORG_INFO_URL: _ok(_ORG_INFO_URL, _ORG_INFO_BODY),
    }
    headers_patch, request_patch = _patched(_router(responses))

    with headers_patch, request_patch:
        result = json.loads(await dataverse_get_organization_info(params, _make_ctx()))

    assert result["version"] == "9.2.0.0"


@pytest.mark.asyncio
async def test_version_omitted_when_not_derivable() -> None:
    """No guessing: the version field is omitted when it cannot be located."""
    params = GetOrganizationInfoInput(dataverse_url=_BASE_URL)
    body = {"@odata.context": "ctx", "A": "one", "B": "two"}
    responses = {
        _VERSION_URL: _ok(_VERSION_URL, body),
        _CURRENT_ORG_URL: _ok(_CURRENT_ORG_URL, _CURRENT_ORG_BODY),
        _ORG_INFO_URL: _ok(_ORG_INFO_URL, _ORG_INFO_BODY),
    }
    headers_patch, request_patch = _patched(_router(responses))

    with headers_patch, request_patch:
        result = json.loads(await dataverse_get_organization_info(params, _make_ctx()))

    assert "version" not in result
    assert result["retrieve_version"] == {"A": "one", "B": "two"}


# ---------------------------------------------------------------------------
# Solution summarization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solutions_are_returned_in_full_when_opted_in() -> None:
    """include_solutions=True adds the array back alongside solutions_count."""
    result = await _run(
        GetOrganizationInfoInput(dataverse_url=_BASE_URL, include_solutions=True),
        _ORG_INFO_BODY,
    )

    container = result["organization_info"]["organizationInfo"]
    assert container["solutions_count"] == 3
    assert container["Solutions"] == _SOLUTIONS
    assert container["InstanceType"] == "Developer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "container",
    [
        pytest.param({"InstanceType": "Developer"}, id="solutions-missing"),
        pytest.param(
            {"InstanceType": "Developer", "Solutions": {"count": "many"}},
            id="solutions-not-a-list",
        ),
    ],
)
async def test_solutions_count_omitted_when_shape_is_unexpected(
    container: dict,
) -> None:
    """A missing or non-list Solutions property is passed through, never counted."""
    result = await _run(
        GetOrganizationInfoInput(dataverse_url=_BASE_URL),
        {"@odata.context": "ctx", "organizationInfo": container},
    )

    assert "error" not in result
    assert result["organization_info"] == {"organizationInfo": container}


def test_include_solutions_defaults_to_false() -> None:
    """The full solution list is opt-in, so the fingerprint stays small by default."""
    assert GetOrganizationInfoInput(dataverse_url=_BASE_URL).include_solutions is False


# ---------------------------------------------------------------------------
# Partial failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_function_failure_is_partial() -> None:
    """A privilege-gated RetrieveOrganizationInfo must not fail the whole tool."""
    params = GetOrganizationInfoInput(dataverse_url=_BASE_URL)
    responses = {
        _VERSION_URL: _ok(_VERSION_URL, _VERSION_BODY),
        _CURRENT_ORG_URL: _ok(_CURRENT_ORG_URL, _CURRENT_ORG_BODY),
        _ORG_INFO_URL: _err(
            _ORG_INFO_URL, 403, "0x80040220", "Principal user is missing prvReadOrganization"
        ),
    }
    headers_patch, request_patch = _patched(_router(responses))

    with headers_patch, request_patch:
        result = json.loads(await dataverse_get_organization_info(params, _make_ctx()))

    assert "error" not in result, "One failed function must not error the whole tool"
    assert result["version"] == "9.2.24073.198"
    assert "current_organization" in result
    assert "organization_info" not in result
    assert len(result["partial_errors"]) == 1
    entry = result["partial_errors"][0]
    assert entry["function"] == "RetrieveOrganizationInfo"
    assert "403" in entry["message"]
    assert "prvReadOrganization" in entry["message"]


@pytest.mark.asyncio
async def test_transport_failure_is_partial() -> None:
    """A non-HTTP failure (transport error) is also recorded as a partial error."""
    params = GetOrganizationInfoInput(dataverse_url=_BASE_URL)

    async def _side_effect(_client, _method, url, **_kwargs):
        if url == _ORG_INFO_URL:
            raise httpx.ReadTimeout("timed out", request=httpx.Request("GET", url))
        return {
            _VERSION_URL: _ok(_VERSION_URL, _VERSION_BODY),
            _CURRENT_ORG_URL: _ok(_CURRENT_ORG_URL, _CURRENT_ORG_BODY),
        }[url]

    headers_patch, request_patch = _patched(_side_effect)

    with headers_patch, request_patch:
        result = json.loads(await dataverse_get_organization_info(params, _make_ctx()))

    assert "error" not in result
    assert "current_organization" in result
    assert [e["function"] for e in result["partial_errors"]] == ["RetrieveOrganizationInfo"]


# ---------------------------------------------------------------------------
# Total failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_failing_returns_error_envelope() -> None:
    """When every function fails the standard error envelope is returned."""
    params = GetOrganizationInfoInput(dataverse_url=_BASE_URL)
    responses = {
        _VERSION_URL: _err(_VERSION_URL, 500, "0x0", "boom"),
        _CURRENT_ORG_URL: _err(_CURRENT_ORG_URL, 500, "0x0", "boom"),
        _ORG_INFO_URL: _err(_ORG_INFO_URL, 500, "0x0", "boom"),
    }
    headers_patch, request_patch = _patched(_router(responses))

    with headers_patch, request_patch:
        result = json.loads(await dataverse_get_organization_info(params, _make_ctx()))

    assert result["error"] is True
    assert "RetrieveVersion" in result["message"]
    assert "RetrieveCurrentOrganization" in result["message"]
    assert "RetrieveOrganizationInfo" in result["message"]
    assert "organization_info" not in result
    assert "partial_errors" not in result


@pytest.mark.asyncio
async def test_auth_failure_returns_error_envelope() -> None:
    """A failure before the three calls (token acquisition) errors the tool."""
    params = GetOrganizationInfoInput(dataverse_url=_BASE_URL)

    with patch(
        "dataverse_mcp.tools.environments.build_headers",
        new=AsyncMock(side_effect=httpx.ConnectError("no route to host")),
    ):
        result = json.loads(await dataverse_get_organization_info(params, _make_ctx()))

    assert result["error"] is True


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "access_type",
    ["Default", "Internet", "Intranet"],
)
def test_access_type_accepts_the_three_enum_members(access_type: str) -> None:
    """All three EndpointAccessType members are accepted."""
    params = GetOrganizationInfoInput(dataverse_url=_BASE_URL, access_type=access_type)
    assert params.access_type == access_type


def test_access_type_defaults_to_default() -> None:
    """access_type defaults to 'Default' when omitted."""
    assert GetOrganizationInfoInput(dataverse_url=_BASE_URL).access_type == "Default"


@pytest.mark.parametrize(
    "access_type",
    [
        "default",  # wrong case — enum member names are case-sensitive
        "Bogus",
        "Default')/WhoAmI(",  # URL breakout attempt
        "",
    ],
)
def test_access_type_rejects_values_outside_the_enum(access_type: str) -> None:
    """Anything outside the three members is rejected before it reaches the URL."""
    with pytest.raises(ValidationError):
        GetOrganizationInfoInput(dataverse_url=_BASE_URL, access_type=access_type)
