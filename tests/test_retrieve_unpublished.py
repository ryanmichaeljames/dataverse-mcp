"""Unit tests for dataverse_retrieve_unpublished.

``RetrieveUnpublished`` is bound to an entity INSTANCE, so the URL is
``{entityset}({record_id})/Microsoft.Dynamics.CRM.RetrieveUnpublished()`` with
``$select`` in the query string. Two values reach that URL: the entity set name,
which lands in a PATH SEGMENT, and the record id, which lands inside a KEY
PREDICATE — the exact input class a confirmed live injection came in through in
this repo. Neither is escaped at the call site, and deliberately so: the entity
set is a closed ``Literal`` (an exhaustive allowlist) and the record id is
GUID-pattern-validated, so no value that reaches the f-string can express ``'``,
``)``, ``?``, ``/`` or CRLF.

Acceptance criteria:
- Happy path: the instance-bound URL is built exactly, the default projection is
  applied, the ``@odata`` envelope is stripped and the record comes back as a
  single record (no fake list, no ``count``).
- ``select`` replaces the default and lands in ``$select`` verbatim, including
  the large XML columns the default holds back.
- An entity set outside the allowlist is refused at the model, so no URL exists.
- An invalid GUID is refused at the model, so no URL exists.
- Unexpected payloads (a list, a scalar, a non-JSON body, an envelope-only body)
  return the error envelope instead of a fabricated record.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import RetrieveUnpublishedInput
from dataverse_mcp.tools.customizations import dataverse_retrieve_unpublished

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"
_FORM_ID = "11111111-2222-3333-4444-555555555555"

_DEFAULT_FORM_SELECT = (
    "formid,name,objecttypecode,type,formactivationstate,isdefault,description"
)

# The exact URL the tool is expected to build for a form with the default
# projection: instance-bound function, bare GUID key predicate, $select in the
# query string.
_EXPECTED_URL = (
    f"{_API_ROOT}/systemforms({_FORM_ID})"
    f"/Microsoft.Dynamics.CRM.RetrieveUnpublished()"
    f"?$select={_DEFAULT_FORM_SELECT}"
)


def _envelope(body: Any) -> Any:
    """Add the @odata annotations the tool is expected to strip."""
    if not isinstance(body, dict):
        return body
    return {
        "@odata.context": f"{_API_ROOT}/$metadata#systemforms/$entity",
        "@odata.etag": 'W/"1234567"',
        **body,
    }


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


async def _run(
    body: Any,
    *,
    status_code: int = 200,
    content: bytes | None = None,
    **kwargs: Any,
) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = RetrieveUnpublishedInput(
        dataverse_url=_BASE_URL,
        entity_set_name=kwargs.pop("entity_set_name", "systemforms"),
        record_id=kwargs.pop("record_id", _FORM_ID),
        **kwargs,
    )

    async def _side_effect(_client, _method, url, **_kwargs):
        request = httpx.Request("GET", url)
        if content is not None:
            return httpx.Response(status_code, content=content, request=request)
        return httpx.Response(status_code, json=body, request=request)

    with patch(
        "dataverse_mcp.tools.customizations.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.customizations.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_retrieve_unpublished(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


@pytest.mark.asyncio
async def test_happy_path_builds_the_instance_bound_url_and_returns_one_record() -> None:
    """The draft record comes back as a record, envelope stripped, not wrapped."""
    record = {
        "formid": _FORM_ID,
        "name": "Account Main",
        "objecttypecode": "account",
        "type": 2,
        "formactivationstate": 1,
        "isdefault": True,
        "description": None,
    }
    result, urls = await _run(_envelope(record))

    assert "error" not in result
    assert urls == [_EXPECTED_URL]
    assert result["entity_set_name"] == "systemforms"
    assert result["record_id"] == _FORM_ID
    assert result["unpublished"] is True
    assert result["select"] == _DEFAULT_FORM_SELECT.split(",")
    # A single record, passed through — not a one-item list, and no count/has_more
    # because nothing here is list-shaped.
    assert result["record"] == record
    assert "count" not in result and "has_more" not in result
    # The default projection holds formxml back and says so.
    assert "formxml" in result["message"]
    # Nothing in the URL needed escaping: the Literal and the GUID pattern are
    # the whole defence, so a stray quote or percent-encoding would be a smell.
    assert "'" not in urls[0]
    assert "%" not in urls[0]


@pytest.mark.asyncio
async def test_select_replaces_the_default_and_opts_into_the_large_xml() -> None:
    """The caller-supplied projection lands in $select verbatim."""
    result, urls = await _run(
        _envelope({"formid": _FORM_ID, "name": "Account Main", "formxml": "<form/>"}),
        select=["formid", "name", "formxml"],
    )

    assert urls == [
        f"{_API_ROOT}/systemforms({_FORM_ID})"
        f"/Microsoft.Dynamics.CRM.RetrieveUnpublished()"
        f"?$select=formid,name,formxml"
    ]
    assert result["select"] == ["formid", "name", "formxml"]
    assert result["record"]["formxml"] == "<form/>"
    # formxml was asked for, so nothing is reported as held back.
    assert "held back" not in result["message"]
    assert "were not requested" not in result["message"]


@pytest.mark.asyncio
async def test_each_allowed_entity_set_gets_its_own_default_projection() -> None:
    """A view is not read with a form's columns."""
    _, urls = await _run(
        _envelope({"savedqueryid": _FORM_ID, "name": "Active Accounts"}),
        entity_set_name="savedqueries",
    )

    assert urls[0].startswith(f"{_API_ROOT}/savedqueries({_FORM_ID})/")
    assert "$select=savedqueryid,name,returnedtypecode" in urls[0]
    assert "fetchxml" not in urls[0] and "layoutxml" not in urls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,content,reason",
    [
        pytest.param(
            {"@odata.context": "ctx", "value": [{"formid": _FORM_ID}]},
            None,
            "a collection where an instance was expected",
            id="collection-shaped",
        ),
        pytest.param(
            [{"formid": _FORM_ID}], None, "a bare JSON list", id="bare-list"
        ),
        pytest.param("OK", None, "a bare JSON string", id="scalar"),
        pytest.param(
            {"@odata.context": "ctx"}, None, "envelope only, no columns", id="empty"
        ),
        pytest.param(None, b"<html>gateway</html>", "a non-JSON body", id="not-json"),
    ],
)
async def test_unexpected_payloads_return_the_error_envelope(
    body: Any, content: bytes | None, reason: str
) -> None:
    """No record is fabricated from a shape the tool does not recognize."""
    result, _ = await _run(body, content=content)

    assert result.get("error") is True, reason
    assert isinstance(result["message"], str) and result["message"]
    assert "record" not in result, f"a record was fabricated from {reason}"


@pytest.mark.asyncio
async def test_missing_record_is_a_clean_404_message() -> None:
    """A well-formed but unknown id is an actionable 404, not a crash."""
    result, _ = await _run(
        {"error": {"code": "0x80040217", "message": "Does Not Exist"}},
        status_code=404,
    )

    assert result["error"] is True
    assert "404" in result["message"]
    assert "record" not in result


@pytest.mark.asyncio
async def test_http_errors_use_the_standard_error_envelope() -> None:
    """A bad column name is a 400 through tool_error_response, not a raised exception."""
    result, _ = await _run(
        {
            "error": {
                "code": "0x0",
                "message": "Could not find a property named 'bogus'",
            }
        },
        status_code=400,
        select=["bogus"],
    )

    assert result["error"] is True
    assert "400" in result["message"]


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        pytest.param(
            {"entity_set_name": "accounts"},
            "a data table with no unpublished layer is outside the allowlist",
            id="not-a-customization-table",
        ),
        pytest.param(
            {"entity_set_name": "sitemaps"},
            "Dataverse refuses the RetrieveUnpublished message for entity type "
            "'sitemap' outright (HTTP 400 0x80040800), with or without $select, so "
            "the value must not be advertised",
            id="sitemaps-unsupported-by-the-platform",
        ),
        pytest.param(
            {"entity_set_name": "systemforms/../accounts"},
            "path traversal in the path segment",
            id="traversal",
        ),
        pytest.param(
            {"entity_set_name": "systemforms?$select=formxml"},
            "query-option injection in the path segment",
            id="query-injection",
        ),
        pytest.param(
            {"entity_set_name": "SystemForms"},
            "the Literal is case-sensitive and the wrong case is a real 404",
            id="wrong-case",
        ),
        pytest.param(
            {"record_id": "not-a-guid"}, "a non-GUID record_id", id="bad-guid"
        ),
        pytest.param(
            {"record_id": _FORM_ID[:-1]}, "a truncated GUID", id="short-guid"
        ),
        pytest.param(
            {"record_id": f"{_FORM_ID})/systemusers("},
            "a key-predicate breakout — the GUID pattern is the only defence",
            id="guid-breakout",
        ),
        pytest.param(
            {"select": ["formid)/WhoAmI("]},
            "a breakout string in a select item",
            id="select-item-breakout",
        ),
        pytest.param({"select": []}, "an empty select list", id="empty-select"),
        pytest.param(
            {"unexpected": "x"}, "extra='forbid' on the model", id="extra-field"
        ),
    ],
)
def test_input_model_rejects_bad_identifiers(kwargs: dict, reason: str) -> None:
    """Bad input is refused at the model boundary, so no URL is ever built."""
    base = {
        "dataverse_url": _BASE_URL,
        "entity_set_name": "systemforms",
        "record_id": _FORM_ID,
    }
    with pytest.raises(ValidationError):
        RetrieveUnpublishedInput(**{**base, **kwargs})
