"""Unit tests for the five web resource tools (issue #94).

Coverage:
- ListWebResourcesInput / GetWebResourceInput / CreateWebResourceInput /
  UpdateWebResourceInput / DeleteWebResourceInput model validation.
- dataverse_list_web_resources: builds correct $filter for type, name_contains,
  both, none; excludes content from URL; count/has_more; webresourcetype_label
  enrichment.
- dataverse_get_web_resource: default excludes content; include_content=true adds
  it; returns {"record": {...}}; strips @odata.context; enriches label.
- dataverse_create_web_resource: POSTs required fields; extracts new id from
  OData-EntityId header; returns {"created": true, "id": ...}.
- dataverse_update_web_resource: PATCHes only provided fields; returns
  {"updated": true, "id": ...}.
- dataverse_update_web_resource: empty update → ValidationError at model level.
- dataverse_delete_web_resource: issues DELETE; returns {"deleted": true, "id": ...}.
- Model validation: bad GUID, extra fields, missing required create fields.
- HTTP error → {"error": true, ...}.

Mocking strategy: patch build_headers to return {} and replace
app_ctx.http_client with an AsyncMock whose .request method returns a crafted
httpx.Response — mirroring test_jobs.py style.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import (
    CreateWebResourceInput,
    DeleteWebResourceInput,
    GetWebResourceInput,
    ListWebResourcesInput,
    UpdateWebResourceInput,
)
from dataverse_mcp.tools.web_resources import (
    _WEBRESOURCETYPE_LABELS,
    dataverse_create_web_resource,
    dataverse_delete_web_resource,
    dataverse_get_web_resource,
    dataverse_list_web_resources,
    dataverse_update_web_resource,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_WR_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_WR_ID2 = "11111111-2222-3333-4444-555555555555"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_ctx() -> AppContext:
    return AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )


def _make_ctx(app_ctx: AppContext) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _make_response(
    status_code: int, body: dict, method: str = "GET", headers: dict | None = None
) -> httpx.Response:
    content = json.dumps(body).encode()
    return httpx.Response(
        status_code=status_code,
        headers={"Content-Type": "application/json", **(headers or {})},
        content=content,
        request=httpx.Request(method, _BASE_URL),
    )


def _make_empty_response(status_code: int, method: str = "DELETE") -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=b"",
        request=httpx.Request(method, _BASE_URL),
    )


# ---------------------------------------------------------------------------
# Input model validation — ListWebResourcesInput
# ---------------------------------------------------------------------------


def test_list_web_resources_input_defaults():
    m = ListWebResourcesInput(dataverse_url=_BASE_URL)
    assert m.top == 50
    assert m.web_resource_type is None
    assert m.name_contains is None
    assert m.select is None


def test_list_web_resources_input_with_filters():
    m = ListWebResourcesInput(
        dataverse_url=_BASE_URL,
        web_resource_type=3,
        name_contains="new_/scripts",
        top=10,
    )
    assert m.web_resource_type == 3
    assert m.name_contains == "new_/scripts"
    assert m.top == 10


def test_list_web_resources_input_top_out_of_range():
    with pytest.raises(ValidationError):
        ListWebResourcesInput(dataverse_url=_BASE_URL, top=0)
    with pytest.raises(ValidationError):
        ListWebResourcesInput(dataverse_url=_BASE_URL, top=5001)


def test_list_web_resources_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        ListWebResourcesInput(dataverse_url=_BASE_URL, bogus="bad")


# ---------------------------------------------------------------------------
# Input model validation — GetWebResourceInput
# ---------------------------------------------------------------------------


def test_get_web_resource_input_valid():
    m = GetWebResourceInput(dataverse_url=_BASE_URL, web_resource_id=_WR_ID)
    assert m.web_resource_id == _WR_ID
    assert m.include_content is False


def test_get_web_resource_input_include_content():
    m = GetWebResourceInput(
        dataverse_url=_BASE_URL, web_resource_id=_WR_ID, include_content=True
    )
    assert m.include_content is True


def test_get_web_resource_input_bad_guid():
    with pytest.raises(ValidationError):
        GetWebResourceInput(dataverse_url=_BASE_URL, web_resource_id="not-a-guid")


def test_get_web_resource_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        GetWebResourceInput(
            dataverse_url=_BASE_URL, web_resource_id=_WR_ID, extra="nope"
        )


# ---------------------------------------------------------------------------
# Input model validation — CreateWebResourceInput
# ---------------------------------------------------------------------------


def test_create_web_resource_input_valid():
    m = CreateWebResourceInput(
        dataverse_url=_BASE_URL,
        name="new_/scripts/app.js",
        web_resource_type=3,
        content="Y29uc29sZS5sb2coJ2hlbGxvJyk7",
    )
    assert m.name == "new_/scripts/app.js"
    assert m.web_resource_type == 3
    assert m.display_name is None
    assert m.description is None


def test_create_web_resource_input_all_fields():
    m = CreateWebResourceInput(
        dataverse_url=_BASE_URL,
        name="new_/scripts/app.js",
        web_resource_type=3,
        content="Y29uc29sZS5sb2coJ2hlbGxvJyk7",
        display_name="My Script",
        description="Application entry point",
    )
    assert m.display_name == "My Script"
    assert m.description == "Application entry point"


def test_create_web_resource_input_missing_name():
    with pytest.raises(ValidationError):
        CreateWebResourceInput(
            dataverse_url=_BASE_URL,
            web_resource_type=3,
            content="Y29u",
        )


def test_create_web_resource_input_missing_type():
    with pytest.raises(ValidationError):
        CreateWebResourceInput(
            dataverse_url=_BASE_URL,
            name="new_/scripts/app.js",
            content="Y29u",
        )


def test_create_web_resource_input_missing_content():
    with pytest.raises(ValidationError):
        CreateWebResourceInput(
            dataverse_url=_BASE_URL,
            name="new_/scripts/app.js",
            web_resource_type=3,
        )


def test_create_web_resource_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        CreateWebResourceInput(
            dataverse_url=_BASE_URL,
            name="new_/scripts/app.js",
            web_resource_type=3,
            content="Y29u",
            unknown_field="bad",
        )


# ---------------------------------------------------------------------------
# Input model validation — UpdateWebResourceInput
# ---------------------------------------------------------------------------


def test_update_web_resource_input_valid_content_only():
    m = UpdateWebResourceInput(
        dataverse_url=_BASE_URL,
        web_resource_id=_WR_ID,
        content="bmV3Y29udGVudA==",
    )
    assert m.content == "bmV3Y29udGVudA=="
    assert m.display_name is None
    assert m.description is None


def test_update_web_resource_input_all_fields():
    m = UpdateWebResourceInput(
        dataverse_url=_BASE_URL,
        web_resource_id=_WR_ID,
        content="bmV3Y29udGVudA==",
        display_name="Updated Script",
        description="New description",
    )
    assert m.display_name == "Updated Script"


def test_update_web_resource_input_no_updatable_fields_raises():
    with pytest.raises(ValidationError, match="At least one updatable field"):
        UpdateWebResourceInput(dataverse_url=_BASE_URL, web_resource_id=_WR_ID)


def test_update_web_resource_input_bad_guid():
    with pytest.raises(ValidationError):
        UpdateWebResourceInput(
            dataverse_url=_BASE_URL,
            web_resource_id="not-a-guid",
            content="Y29u",
        )


def test_update_web_resource_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        UpdateWebResourceInput(
            dataverse_url=_BASE_URL,
            web_resource_id=_WR_ID,
            content="Y29u",
            unknown="x",
        )


# ---------------------------------------------------------------------------
# Input model validation — DeleteWebResourceInput
# ---------------------------------------------------------------------------


def test_delete_web_resource_input_valid():
    m = DeleteWebResourceInput(dataverse_url=_BASE_URL, web_resource_id=_WR_ID)
    assert m.web_resource_id == _WR_ID


def test_delete_web_resource_input_bad_guid():
    with pytest.raises(ValidationError):
        DeleteWebResourceInput(dataverse_url=_BASE_URL, web_resource_id="bad")


def test_delete_web_resource_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        DeleteWebResourceInput(
            dataverse_url=_BASE_URL, web_resource_id=_WR_ID, extra="nope"
        )


# ---------------------------------------------------------------------------
# dataverse_list_web_resources — filter URL construction
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.web_resources.paginate_records", new_callable=AsyncMock)
async def test_list_web_resources_no_filter(mock_paginate, mock_headers):
    """No filters: $filter absent from URL; count/has_more returned."""
    mock_records = [
        {
            "webresourceid": _WR_ID,
            "name": "new_/scripts/app.js",
            "webresourcetype": 3,
        }
    ]
    mock_paginate.return_value = mock_records
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListWebResourcesInput(dataverse_url=_BASE_URL, top=10)
    result = json.loads(await dataverse_list_web_resources(params, ctx))

    assert result["count"] == 1
    assert result["has_more"] is False
    assert result["records"][0]["webresourceid"] == _WR_ID

    called_url = mock_paginate.call_args.args[0]
    assert "$filter" not in called_url
    assert "content" not in called_url


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.web_resources.paginate_records", new_callable=AsyncMock)
async def test_list_web_resources_type_filter(mock_paginate, mock_headers):
    """web_resource_type filter adds 'webresourcetype eq N' to $filter."""
    mock_paginate.return_value = []
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListWebResourcesInput(dataverse_url=_BASE_URL, web_resource_type=3)
    await dataverse_list_web_resources(params, ctx)

    called_url = mock_paginate.call_args.args[0]
    assert "webresourcetype" in called_url
    assert "3" in called_url


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.web_resources.paginate_records", new_callable=AsyncMock)
async def test_list_web_resources_name_contains_filter(mock_paginate, mock_headers):
    """name_contains filter adds contains(name,'...') to $filter."""
    mock_paginate.return_value = []
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListWebResourcesInput(dataverse_url=_BASE_URL, name_contains="new_/")
    await dataverse_list_web_resources(params, ctx)

    called_url = mock_paginate.call_args.args[0]
    assert "contains" in called_url
    assert "name" in called_url


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.web_resources.paginate_records", new_callable=AsyncMock)
async def test_list_web_resources_both_filters(mock_paginate, mock_headers):
    """Both filters combine with ' and '."""
    mock_paginate.return_value = []
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListWebResourcesInput(
        dataverse_url=_BASE_URL, web_resource_type=3, name_contains="new_/"
    )
    await dataverse_list_web_resources(params, ctx)

    called_url = mock_paginate.call_args.args[0]
    assert "webresourcetype" in called_url
    assert "contains" in called_url


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.web_resources.paginate_records", new_callable=AsyncMock)
async def test_list_web_resources_excludes_content(mock_paginate, mock_headers):
    """content field must never appear in the list $select."""
    mock_paginate.return_value = []
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListWebResourcesInput(dataverse_url=_BASE_URL)
    await dataverse_list_web_resources(params, ctx)

    called_url = mock_paginate.call_args.args[0]
    # content should not be in the $select parameter
    import urllib.parse
    parsed = urllib.parse.urlparse(called_url)
    qs = urllib.parse.parse_qs(parsed.query)
    select_val = qs.get("$select", [""])[0]
    assert "content" not in select_val.split(",")


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.web_resources.paginate_records", new_callable=AsyncMock)
async def test_list_web_resources_has_more(mock_paginate, mock_headers):
    """has_more is True when returned record count equals top."""
    top = 3
    mock_paginate.return_value = [
        {"webresourceid": f"{i}" * 8 + "-0000-0000-0000-" + "0" * 12}
        for i in range(top)
    ]
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListWebResourcesInput(dataverse_url=_BASE_URL, top=top)
    result = json.loads(await dataverse_list_web_resources(params, ctx))

    assert result["has_more"] is True
    assert result["count"] == top


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.web_resources.paginate_records", new_callable=AsyncMock)
async def test_list_web_resources_enriches_label(mock_paginate, mock_headers):
    """Records are enriched with webresourcetype_label."""
    mock_paginate.return_value = [
        {"webresourceid": _WR_ID, "webresourcetype": 3}
    ]
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListWebResourcesInput(dataverse_url=_BASE_URL)
    result = json.loads(await dataverse_list_web_resources(params, ctx))

    assert result["records"][0]["webresourcetype_label"] == "JScript"


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.web_resources.paginate_records", new_callable=AsyncMock)
async def test_list_web_resources_http_error(mock_paginate, mock_headers):
    """HTTP error from paginate_records returns {"error": true, ...}."""
    mock_paginate.side_effect = httpx.HTTPStatusError(
        "500 Internal Server Error",
        request=httpx.Request("GET", _BASE_URL),
        response=httpx.Response(
            500,
            content=b'{"error":{"message":"server error"}}',
            request=httpx.Request("GET", _BASE_URL),
        ),
    )
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListWebResourcesInput(dataverse_url=_BASE_URL)
    result = json.loads(await dataverse_list_web_resources(params, ctx))

    assert result["error"] is True
    assert "message" in result


# ---------------------------------------------------------------------------
# dataverse_get_web_resource
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_web_resource_default_excludes_content(mock_headers):
    """Default get: content NOT in $select; returns {"record": {...}}."""
    record_body = {
        "@odata.context": "https://yourorg.crm.dynamics.com/api/data/v9.2/$metadata#webresources/$entity",
        "webresourceid": _WR_ID,
        "name": "new_/scripts/app.js",
        "webresourcetype": 3,
    }
    resp = _make_response(200, record_body)
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=resp)
    ctx = _make_ctx(app_ctx)

    params = GetWebResourceInput(dataverse_url=_BASE_URL, web_resource_id=_WR_ID)
    result = json.loads(await dataverse_get_web_resource(params, ctx))

    assert "record" in result
    assert result["record"]["webresourceid"] == _WR_ID
    assert "@odata.context" not in result["record"]

    # content must not appear in the URL select
    call_args = app_ctx.http_client.request.call_args
    url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url", "")
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    select_val = qs.get("$select", [""])[0]
    assert "content" not in select_val.split(",")


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_web_resource_include_content_adds_field(mock_headers):
    """include_content=True adds 'content' to the $select URL parameter."""
    record_body = {
        "webresourceid": _WR_ID,
        "name": "new_/scripts/app.js",
        "webresourcetype": 3,
        "content": "Y29uc29sZS5sb2coJ2hlbGxvJyk7",
    }
    resp = _make_response(200, record_body)
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=resp)
    ctx = _make_ctx(app_ctx)

    params = GetWebResourceInput(
        dataverse_url=_BASE_URL, web_resource_id=_WR_ID, include_content=True
    )
    result = json.loads(await dataverse_get_web_resource(params, ctx))

    assert "record" in result

    call_args = app_ctx.http_client.request.call_args
    url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url", "")
    assert "content" in url


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_web_resource_enriches_label(mock_headers):
    """Get enriches webresourcetype_label on the record."""
    record_body = {"webresourceid": _WR_ID, "webresourcetype": 1}
    resp = _make_response(200, record_body)
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=resp)
    ctx = _make_ctx(app_ctx)

    params = GetWebResourceInput(dataverse_url=_BASE_URL, web_resource_id=_WR_ID)
    result = json.loads(await dataverse_get_web_resource(params, ctx))

    assert result["record"]["webresourcetype_label"] == "HTML"


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_web_resource_404_returns_error(mock_headers):
    """404 response returns a structured error."""
    not_found = httpx.Response(
        status_code=404,
        content=b'{"error":{"code":"0x80040217","message":"Object not found"}}',
        request=httpx.Request("GET", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=not_found)
    ctx = _make_ctx(app_ctx)

    params = GetWebResourceInput(dataverse_url=_BASE_URL, web_resource_id=_WR_ID)
    result = json.loads(await dataverse_get_web_resource(params, ctx))

    assert result["error"] is True
    assert "message" in result


# ---------------------------------------------------------------------------
# dataverse_create_web_resource
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_create_web_resource_returns_new_id(mock_headers):
    """Create returns {"created": true, "id": <guid from OData-EntityId>}."""
    entity_id_uri = (
        f"https://yourorg.crm.dynamics.com/api/data/v9.2/webresources({_WR_ID2})"
    )
    post_resp = httpx.Response(
        status_code=204,
        headers={"OData-EntityId": entity_id_uri},
        content=b"",
        request=httpx.Request("POST", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=post_resp)
    ctx = _make_ctx(app_ctx)

    params = CreateWebResourceInput(
        dataverse_url=_BASE_URL,
        name="new_/scripts/app.js",
        web_resource_type=3,
        content="Y29uc29sZS5sb2coJ2hlbGxvJyk7",
    )
    result = json.loads(await dataverse_create_web_resource(params, ctx))

    assert result["created"] is True
    assert result["id"] == _WR_ID2


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_create_web_resource_posts_required_fields(mock_headers):
    """Create POSTs name, webresourcetype, content."""
    entity_id_uri = (
        f"https://yourorg.crm.dynamics.com/api/data/v9.2/webresources({_WR_ID2})"
    )
    post_resp = httpx.Response(
        status_code=204,
        headers={"OData-EntityId": entity_id_uri},
        content=b"",
        request=httpx.Request("POST", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=post_resp)
    ctx = _make_ctx(app_ctx)

    params = CreateWebResourceInput(
        dataverse_url=_BASE_URL,
        name="new_/scripts/app.js",
        web_resource_type=3,
        content="Y29uc29sZS5sb2coJ2hlbGxvJyk7",
        display_name="My Script",
    )
    await dataverse_create_web_resource(params, ctx)

    call_args = app_ctx.http_client.request.call_args
    body = call_args.kwargs.get("json", {})
    assert body["name"] == "new_/scripts/app.js"
    assert body["webresourcetype"] == 3
    assert body["content"] == "Y29uc29sZS5sb2coJ2hlbGxvJyk7"
    assert body["displayname"] == "My Script"


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_create_web_resource_posts_to_webresources_endpoint(mock_headers):
    """Create POSTs to the webresources entity set endpoint."""
    entity_id_uri = (
        f"https://yourorg.crm.dynamics.com/api/data/v9.2/webresources({_WR_ID2})"
    )
    post_resp = httpx.Response(
        status_code=204,
        headers={"OData-EntityId": entity_id_uri},
        content=b"",
        request=httpx.Request("POST", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=post_resp)
    ctx = _make_ctx(app_ctx)

    params = CreateWebResourceInput(
        dataverse_url=_BASE_URL,
        name="new_/scripts/app.js",
        web_resource_type=3,
        content="Y29uc29sZS5sb2coJ2hlbGxvJyk7",
    )
    await dataverse_create_web_resource(params, ctx)

    call_args = app_ctx.http_client.request.call_args
    url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url", "")
    assert "webresourceset" in url
    method = call_args.args[0] if call_args.args else call_args.kwargs.get("method")
    assert method == "POST"


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_create_web_resource_http_error(mock_headers):
    """HTTP 4xx returns {"error": true, "message": ...}."""
    bad_resp = httpx.Response(
        status_code=400,
        content=b'{"error":{"code":"0x80040203","message":"Invalid request"}}',
        request=httpx.Request("POST", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=bad_resp)
    ctx = _make_ctx(app_ctx)

    params = CreateWebResourceInput(
        dataverse_url=_BASE_URL,
        name="new_/scripts/app.js",
        web_resource_type=3,
        content="Y29u",
    )
    result = json.loads(await dataverse_create_web_resource(params, ctx))

    assert result["error"] is True
    assert "message" in result


# ---------------------------------------------------------------------------
# dataverse_update_web_resource
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_update_web_resource_patches_only_provided_fields(mock_headers):
    """Update sends only content (when only content is provided)."""
    patch_resp = _make_empty_response(204, method="PATCH")
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=patch_resp)
    ctx = _make_ctx(app_ctx)

    params = UpdateWebResourceInput(
        dataverse_url=_BASE_URL,
        web_resource_id=_WR_ID,
        content="bmV3Y29udGVudA==",
    )
    result = json.loads(await dataverse_update_web_resource(params, ctx))

    assert result["updated"] is True
    assert result["id"] == _WR_ID

    call_args = app_ctx.http_client.request.call_args
    body = call_args.kwargs.get("json", {})
    assert "content" in body
    assert "displayname" not in body
    assert "description" not in body


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_update_web_resource_patches_multiple_fields(mock_headers):
    """Update sends all provided fields."""
    patch_resp = _make_empty_response(204, method="PATCH")
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=patch_resp)
    ctx = _make_ctx(app_ctx)

    params = UpdateWebResourceInput(
        dataverse_url=_BASE_URL,
        web_resource_id=_WR_ID,
        content="bmV3Y29udGVudA==",
        display_name="Updated",
        description="New desc",
    )
    await dataverse_update_web_resource(params, ctx)

    call_args = app_ctx.http_client.request.call_args
    body = call_args.kwargs.get("json", {})
    assert body["content"] == "bmV3Y29udGVudA=="
    assert body["displayname"] == "Updated"
    assert body["description"] == "New desc"


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_update_web_resource_url_contains_id(mock_headers):
    """PATCH URL contains the webresource GUID."""
    patch_resp = _make_empty_response(204, method="PATCH")
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=patch_resp)
    ctx = _make_ctx(app_ctx)

    params = UpdateWebResourceInput(
        dataverse_url=_BASE_URL,
        web_resource_id=_WR_ID,
        display_name="Updated",
    )
    await dataverse_update_web_resource(params, ctx)

    call_args = app_ctx.http_client.request.call_args
    url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url", "")
    assert _WR_ID in url
    assert "webresourceset" in url


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_update_web_resource_http_error(mock_headers):
    """HTTP 4xx returns {"error": true, ...}."""
    bad_resp = httpx.Response(
        status_code=404,
        content=b'{"error":{"code":"0x80040217","message":"Object not found"}}',
        request=httpx.Request("PATCH", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=bad_resp)
    ctx = _make_ctx(app_ctx)

    params = UpdateWebResourceInput(
        dataverse_url=_BASE_URL,
        web_resource_id=_WR_ID,
        content="Y29u",
    )
    result = json.loads(await dataverse_update_web_resource(params, ctx))

    assert result["error"] is True
    assert "message" in result


# ---------------------------------------------------------------------------
# dataverse_delete_web_resource
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_delete_web_resource_returns_deleted(mock_headers):
    """Successful DELETE returns {"deleted": true, "id": ...}."""
    del_resp = _make_empty_response(204)
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=del_resp)
    ctx = _make_ctx(app_ctx)

    params = DeleteWebResourceInput(dataverse_url=_BASE_URL, web_resource_id=_WR_ID)
    result = json.loads(await dataverse_delete_web_resource(params, ctx))

    assert result["deleted"] is True
    assert result["id"] == _WR_ID


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_delete_web_resource_issues_delete_to_correct_url(mock_headers):
    """DELETE issues to webresources(<guid>)."""
    del_resp = _make_empty_response(204)
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=del_resp)
    ctx = _make_ctx(app_ctx)

    params = DeleteWebResourceInput(dataverse_url=_BASE_URL, web_resource_id=_WR_ID)
    await dataverse_delete_web_resource(params, ctx)

    call_args = app_ctx.http_client.request.call_args
    url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url", "")
    assert _WR_ID in url
    assert "webresourceset" in url
    method = call_args.args[0] if call_args.args else call_args.kwargs.get("method")
    assert method == "DELETE"


@patch("dataverse_mcp.tools.web_resources.build_headers", new_callable=AsyncMock, return_value={})
async def test_delete_web_resource_http_error(mock_headers):
    """HTTP 4xx from delete returns {"error": true, ...}."""
    bad_resp = httpx.Response(
        status_code=404,
        content=b'{"error":{"code":"0x80040217","message":"Object not found"}}',
        request=httpx.Request("DELETE", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=bad_resp)
    ctx = _make_ctx(app_ctx)

    params = DeleteWebResourceInput(dataverse_url=_BASE_URL, web_resource_id=_WR_ID)
    result = json.loads(await dataverse_delete_web_resource(params, ctx))

    assert result["error"] is True
    assert "message" in result


# ---------------------------------------------------------------------------
# webresourcetype label map sanity checks
# ---------------------------------------------------------------------------


def test_webresourcetype_label_map_covers_all_standard_values():
    """Verify the label map covers all 12 standard webresourcetype values."""
    assert _WEBRESOURCETYPE_LABELS[1] == "HTML"
    assert _WEBRESOURCETYPE_LABELS[2] == "CSS"
    assert _WEBRESOURCETYPE_LABELS[3] == "JScript"
    assert _WEBRESOURCETYPE_LABELS[4] == "XML"
    assert _WEBRESOURCETYPE_LABELS[5] == "PNG"
    assert _WEBRESOURCETYPE_LABELS[6] == "JPG"
    assert _WEBRESOURCETYPE_LABELS[7] == "GIF"
    assert _WEBRESOURCETYPE_LABELS[10] == "ICO"
    assert _WEBRESOURCETYPE_LABELS[11] == "Vector (SVG)"
    assert _WEBRESOURCETYPE_LABELS[12] == "String (RESX)"
    assert len(_WEBRESOURCETYPE_LABELS) == 12
