"""Unit tests for alternate key (EntityKeyMetadata) tools and input models.

Coverage:
- ListAlternateKeysInput / CreateAlternateKeyInput / DeleteAlternateKeyInput
  model validation (required fields, GUID-free, key_attributes normalisation,
  extra='forbid').
- dataverse_list_alternate_keys happy path (mocked paginate_records).
- dataverse_create_alternate_key success path — async index status surfaced.
- dataverse_delete_alternate_key success path.
- dataverse_delete_alternate_key 404 → structured error (not raise).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import (
    CreateAlternateKeyInput,
    DeleteAlternateKeyInput,
    ListAlternateKeysInput,
)
from dataverse_mcp.tools.metadata import (
    dataverse_create_alternate_key,
    dataverse_delete_alternate_key,
    dataverse_list_alternate_keys,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_TABLE = "account"
_KEY_LOGICAL = "new_accountcode"
_KEY_SCHEMA = "new_AccountCode"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_ctx() -> AppContext:
    """Return a minimal AppContext with a mock http_client."""
    return AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )


def _make_ctx(app_ctx: AppContext) -> MagicMock:
    """Return a mock FastMCP Context backed by *app_ctx*."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _mock_response(
    status_code: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Input model validation — ListAlternateKeysInput
# ---------------------------------------------------------------------------


def test_list_alternate_keys_input_valid() -> None:
    """ListAlternateKeysInput accepts required fields."""
    m = ListAlternateKeysInput(dataverse_url=_BASE_URL, table_logical_name=_TABLE)
    assert m.table_logical_name == _TABLE
    assert m.top == 50


def test_list_alternate_keys_input_missing_table() -> None:
    """ListAlternateKeysInput rejects missing table_logical_name."""
    with pytest.raises(Exception):
        ListAlternateKeysInput(dataverse_url=_BASE_URL)


def test_list_alternate_keys_input_forbids_extra() -> None:
    """ListAlternateKeysInput rejects unknown extra fields."""
    with pytest.raises(Exception):
        ListAlternateKeysInput(
            dataverse_url=_BASE_URL,
            table_logical_name=_TABLE,
            unknown_field="oops",
        )


# ---------------------------------------------------------------------------
# Input model validation — CreateAlternateKeyInput
# ---------------------------------------------------------------------------


def test_create_alternate_key_input_valid() -> None:
    """CreateAlternateKeyInput accepts required fields."""
    m = CreateAlternateKeyInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        schema_name=_KEY_SCHEMA,
        display_name="Account Code",
        key_attributes=["accountnumber"],
    )
    assert m.schema_name == _KEY_SCHEMA
    assert m.key_attributes == ["accountnumber"]
    assert m.solution_unique_name is None


def test_create_alternate_key_input_normalises_attributes() -> None:
    """key_attributes are lowercased and stripped by the validator."""
    m = CreateAlternateKeyInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        schema_name=_KEY_SCHEMA,
        display_name="Account Code",
        key_attributes=["  AccountNumber  ", "ExternalID"],
    )
    assert m.key_attributes == ["accountnumber", "externalid"]


def test_create_alternate_key_input_rejects_empty_attributes() -> None:
    """key_attributes must not be empty."""
    with pytest.raises(Exception):
        CreateAlternateKeyInput(
            dataverse_url=_BASE_URL,
            table_logical_name=_TABLE,
            schema_name=_KEY_SCHEMA,
            display_name="Account Code",
            key_attributes=[],
        )


def test_create_alternate_key_input_rejects_blank_attribute_name() -> None:
    """key_attributes must not contain blank strings."""
    with pytest.raises(Exception):
        CreateAlternateKeyInput(
            dataverse_url=_BASE_URL,
            table_logical_name=_TABLE,
            schema_name=_KEY_SCHEMA,
            display_name="Account Code",
            key_attributes=["accountnumber", "   "],
        )


def test_create_alternate_key_input_missing_schema_name() -> None:
    """CreateAlternateKeyInput rejects missing schema_name."""
    with pytest.raises(Exception):
        CreateAlternateKeyInput(
            dataverse_url=_BASE_URL,
            table_logical_name=_TABLE,
            display_name="Account Code",
            key_attributes=["accountnumber"],
        )


def test_create_alternate_key_input_forbids_extra() -> None:
    """CreateAlternateKeyInput rejects unknown extra fields."""
    with pytest.raises(Exception):
        CreateAlternateKeyInput(
            dataverse_url=_BASE_URL,
            table_logical_name=_TABLE,
            schema_name=_KEY_SCHEMA,
            display_name="Account Code",
            key_attributes=["accountnumber"],
            surprise_field="no",
        )


# ---------------------------------------------------------------------------
# Input model validation — DeleteAlternateKeyInput
# ---------------------------------------------------------------------------


def test_delete_alternate_key_input_valid() -> None:
    """DeleteAlternateKeyInput accepts required fields."""
    m = DeleteAlternateKeyInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        key_logical_name=_KEY_LOGICAL,
    )
    assert m.key_logical_name == _KEY_LOGICAL


def test_delete_alternate_key_input_missing_key_logical_name() -> None:
    """DeleteAlternateKeyInput rejects missing key_logical_name."""
    with pytest.raises(Exception):
        DeleteAlternateKeyInput(dataverse_url=_BASE_URL, table_logical_name=_TABLE)


def test_delete_alternate_key_input_forbids_extra() -> None:
    """DeleteAlternateKeyInput rejects unknown extra fields."""
    with pytest.raises(Exception):
        DeleteAlternateKeyInput(
            dataverse_url=_BASE_URL,
            table_logical_name=_TABLE,
            key_logical_name=_KEY_LOGICAL,
            nope="nope",
        )


# ---------------------------------------------------------------------------
# Tool — dataverse_list_alternate_keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_alternate_keys_happy_path() -> None:
    """dataverse_list_alternate_keys returns keys with count and has_more."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ListAlternateKeysInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        top=50,
    )

    fake_keys = [
        {
            "SchemaName": _KEY_SCHEMA,
            "LogicalName": _KEY_LOGICAL,
            "KeyAttributes": ["accountnumber"],
            "EntityKeyIndexStatus": "Active",
        }
    ]

    with (
        patch("dataverse_mcp.tools.metadata.build_headers", new_callable=AsyncMock) as mock_headers,
        patch("dataverse_mcp.tools.metadata.paginate_records", new_callable=AsyncMock) as mock_page,
        patch("dataverse_mcp.tools.metadata.resolve_base_url", return_value=_BASE_URL),
    ):
        mock_headers.return_value = {}
        mock_page.return_value = fake_keys

        result = await dataverse_list_alternate_keys(params, ctx)

    data = json.loads(result)
    assert data["count"] == 1
    assert data["alternate_keys"][0]["LogicalName"] == _KEY_LOGICAL
    assert data["has_more"] is False


@pytest.mark.asyncio
async def test_list_alternate_keys_bad_url() -> None:
    """dataverse_list_alternate_keys returns error dict on invalid URL."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ListAlternateKeysInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
    )

    with patch(
        "dataverse_mcp.tools.metadata.resolve_base_url",
        side_effect=ValueError("bad url"),
    ):
        result = await dataverse_list_alternate_keys(params, ctx)

    data = json.loads(result)
    assert data["error"] is True
    assert "bad url" in data["message"]


# ---------------------------------------------------------------------------
# Tool — dataverse_create_alternate_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_alternate_key_happy_path() -> None:
    """dataverse_create_alternate_key returns created=True with async status."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = CreateAlternateKeyInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        schema_name=_KEY_SCHEMA,
        display_name="Account Code",
        key_attributes=["accountnumber"],
    )

    resp = _mock_response(
        status_code=201,
        json_body={
            "MetadataId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "EntityKeyIndexStatus": "Pending",
            "AsyncJob": {"Id": "11111111-2222-3333-4444-555555555555"},
        },
        headers={"OData-EntityId": f"{_BASE_URL}/api/data/v9.2/EntityDefinitions(LogicalName='account')/Keys(aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)"},
    )

    with (
        patch("dataverse_mcp.tools.metadata.build_headers", new_callable=AsyncMock) as mock_headers,
        patch("dataverse_mcp.tools.metadata.request_with_retry", new_callable=AsyncMock) as mock_req,
        patch("dataverse_mcp.tools.metadata.resolve_base_url", return_value=_BASE_URL),
    ):
        mock_headers.return_value = {}
        mock_req.return_value = resp

        result = await dataverse_create_alternate_key(params, ctx)

    data = json.loads(result)
    assert data["created"] is True
    assert data["table_logical_name"] == _TABLE
    assert data["schema_name"] == _KEY_SCHEMA
    assert data["logical_name"] == _KEY_SCHEMA.lower()
    assert data["key_attributes"] == ["accountnumber"]
    assert data["entity_key_index_status"] == "Pending"
    assert data["async_job_id"] == "11111111-2222-3333-4444-555555555555"
    assert "note" in data


@pytest.mark.asyncio
async def test_create_alternate_key_bad_url() -> None:
    """dataverse_create_alternate_key returns error dict on invalid URL."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = CreateAlternateKeyInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        schema_name=_KEY_SCHEMA,
        display_name="Account Code",
        key_attributes=["accountnumber"],
    )

    with patch(
        "dataverse_mcp.tools.metadata.resolve_base_url",
        side_effect=ValueError("bad url"),
    ):
        result = await dataverse_create_alternate_key(params, ctx)

    data = json.loads(result)
    assert data["error"] is True


@pytest.mark.asyncio
async def test_create_alternate_key_request_body_includes_odata_type() -> None:
    """dataverse_create_alternate_key sends the correct @odata.type in the request body."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = CreateAlternateKeyInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        schema_name=_KEY_SCHEMA,
        display_name="Account Code",
        key_attributes=["accountnumber"],
    )

    resp = _mock_response(
        status_code=201,
        json_body={"EntityKeyIndexStatus": "Pending"},
        headers={},
    )

    with (
        patch("dataverse_mcp.tools.metadata.build_headers", new_callable=AsyncMock) as mock_headers,
        patch("dataverse_mcp.tools.metadata.request_with_retry", new_callable=AsyncMock) as mock_req,
        patch("dataverse_mcp.tools.metadata.resolve_base_url", return_value=_BASE_URL),
    ):
        mock_headers.return_value = {}
        mock_req.return_value = resp

        await dataverse_create_alternate_key(params, ctx)

    # Verify the POST body had the correct odata type and key attributes
    call_kwargs = mock_req.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs.args[3] if len(call_kwargs.args) > 3 else call_kwargs.kwargs.get("json", {})
    assert body.get("@odata.type") == "Microsoft.Dynamics.CRM.EntityKeyMetadata"
    assert body.get("SchemaName") == _KEY_SCHEMA
    assert body.get("KeyAttributes") == ["accountnumber"]


# ---------------------------------------------------------------------------
# Tool — dataverse_delete_alternate_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_alternate_key_happy_path() -> None:
    """dataverse_delete_alternate_key returns deleted=True on 204."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = DeleteAlternateKeyInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        key_logical_name=_KEY_LOGICAL,
    )

    resp = _mock_response(status_code=204)

    with (
        patch("dataverse_mcp.tools.metadata.build_headers", new_callable=AsyncMock) as mock_headers,
        patch("dataverse_mcp.tools.metadata.request_with_retry", new_callable=AsyncMock) as mock_req,
        patch("dataverse_mcp.tools.metadata.resolve_base_url", return_value=_BASE_URL),
    ):
        mock_headers.return_value = {}
        mock_req.return_value = resp

        result = await dataverse_delete_alternate_key(params, ctx)

    data = json.loads(result)
    assert data["deleted"] is True
    assert data["table_logical_name"] == _TABLE
    assert data["key_logical_name"] == _KEY_LOGICAL


@pytest.mark.asyncio
async def test_delete_alternate_key_not_found() -> None:
    """dataverse_delete_alternate_key returns structured error on 404 — does not raise."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = DeleteAlternateKeyInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        key_logical_name="nonexistent_key",
    )

    http_err = httpx.HTTPStatusError(
        "Not Found",
        request=MagicMock(),
        response=MagicMock(status_code=404),
    )

    with (
        patch("dataverse_mcp.tools.metadata.build_headers", new_callable=AsyncMock) as mock_headers,
        patch("dataverse_mcp.tools.metadata.request_with_retry", new_callable=AsyncMock) as mock_req,
        patch("dataverse_mcp.tools.metadata.resolve_base_url", return_value=_BASE_URL),
    ):
        mock_headers.return_value = {}
        mock_req.side_effect = http_err

        result = await dataverse_delete_alternate_key(params, ctx)

    data = json.loads(result)
    assert data["error"] is True
    assert "nonexistent_key" in data["message"]
    assert "not found" in data["message"].lower()


@pytest.mark.asyncio
async def test_delete_alternate_key_bad_url() -> None:
    """dataverse_delete_alternate_key returns error dict on invalid URL."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = DeleteAlternateKeyInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        key_logical_name=_KEY_LOGICAL,
    )

    with patch(
        "dataverse_mcp.tools.metadata.resolve_base_url",
        side_effect=ValueError("bad url"),
    ):
        result = await dataverse_delete_alternate_key(params, ctx)

    data = json.loads(result)
    assert data["error"] is True
