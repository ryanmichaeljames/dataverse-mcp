"""Unit tests for dataverse_retrieve_access_origin.

``RetrieveAccessOrigin`` is an unbound GET taking THREE parameters of TWO
different OData types, which is why the URL assertions below matter more than
usual:

* ``ObjectId`` and ``PrincipalId`` are ``Edm.Guid`` — written BARE, no quotes, no
  ``guid'…'`` prefix, nothing percent-encoded;
* ``LogicalName`` is ``Edm.String`` — written as a single-quoted OData string
  literal, so ``encode_odata_literal`` DOES apply to it and only to it.

``LogicalName`` is the highest-risk input: this repo has a confirmed live OData
key-predicate injection through exactly this class of schema-plane logical-name
value. It is defended twice — the input model's identifier grammar, and
``encode_odata_literal`` at the build site — and the breakout tests below assert
the first layer stops it before any URL exists.

The response shape is now LIVE-VERIFIED: the body is exactly
``{"Response": "<string>"}``, one scalar string property, never a collection. The
fixtures below use that shape. The important behaviour these tests pin is that
"no access", "access via ownership" and "that record does not exist" all arrive
as an ordinary HTTP 200 carrying that same single string, and the tool passes the
prose through WITHOUT classifying it.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import RetrieveAccessOriginInput
from dataverse_mcp.tools.security import dataverse_retrieve_access_origin

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"
_OBJECT_ID = "11111111-2222-3333-4444-555555555555"
_PRINCIPAL_ID = "99999999-8888-7777-6666-555555555555"
_LOGICAL_NAME = "account"

# The exact URL the tool is expected to build: two bare Guid literals and one
# single-quoted string literal, each behind its own parameter alias.
_EXPECTED_URL = (
    f"{_API_ROOT}/RetrieveAccessOrigin(ObjectId=@oid,LogicalName=@ln,PrincipalId=@pid)"
    f"?@oid={_OBJECT_ID}&@ln='{_LOGICAL_NAME}'&@pid={_PRINCIPAL_ID}"
)

# The three Response strings observed live, verbatim apart from a redacted GUID.
_OWNER_TEXT = f"PrincipalId is object owner ({_PRINCIPAL_ID})"
_NO_ACCESS_TEXT = (
    "Access origin could not be found. Access does not come from POA table or "
    "object ownership."
)
_ABSENT_RECORD_TEXT = (
    "System.ServiceModel.FaultException`1[...]: account With Id = "
    f"{_OBJECT_ID} Does Not Exist"
)


def _envelope(body: Any) -> Any:
    if not isinstance(body, dict):
        return body
    return {"@odata.context": f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM.x", **body}


def _make_ctx() -> MagicMock:
    app_ctx = AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _run(
    body: Any, *, status_code: int = 200, **kwargs: Any
) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = RetrieveAccessOriginInput(
        dataverse_url=_BASE_URL,
        object_id=_OBJECT_ID,
        logical_name=_LOGICAL_NAME,
        principal_id=_PRINCIPAL_ID,
        **kwargs,
    )

    async def _side_effect(_client, _method, url, **_kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(status_code, json=body, request=request)

    with patch(
        "dataverse_mcp.tools.security.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.security.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_retrieve_access_origin(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


@pytest.mark.asyncio
async def test_happy_path_mixes_both_escaping_regimes_in_one_url() -> None:
    """The Guids go in bare; the logical name goes in as a quoted OData literal."""
    body = {"Response": _OWNER_TEXT}
    result, urls = await _run(_envelope(body))

    assert "error" not in result
    assert result["normalized"] is True
    assert result["access_origin_source"] == "Response"
    assert result["access_origin"] == _OWNER_TEXT
    assert "count" not in result, "a single scalar answer is never list-shaped"
    # The @odata.* envelope is stripped, everything else is passed through.
    assert result["raw_response"] == body
    assert result["object_id"] == _OBJECT_ID
    assert result["logical_name"] == _LOGICAL_NAME
    assert result["principal_id"] == _PRINCIPAL_ID

    assert urls == [_EXPECTED_URL], "the tool is exactly one round trip"
    # Edm.Guid literals are bare: neither GUID is quoted or encoded anywhere.
    assert f"@oid={_OBJECT_ID}&" in urls[0]
    assert urls[0].endswith(f"@pid={_PRINCIPAL_ID}")
    # Edm.String is quoted; the only quotes in the URL are the two delimiting it.
    assert urls[0].count("'") == 2
    assert f"@ln='{_LOGICAL_NAME}'" in urls[0]
    assert "guid" not in urls[0]
    assert "%" not in urls[0], "a plain identifier needs no percent-encoding"


@pytest.mark.asyncio
async def test_all_three_live_outcomes_are_one_shape_and_are_not_classified() -> None:
    """HTTP 200 does not mean access. All three outcomes look identical bar prose.

    This is the trap the tool exists to make visible: a caller that treats a
    successful call as "access confirmed" is wrong two times in three. Asserted in
    one test on purpose — the three strings share a single code path, so this
    documents the live wording without inflating the suite. The prose is passed
    through VERBATIM: no boolean, no has_access, no error flag is derived from it.
    """
    outcomes = {
        "access exists, via ownership": _OWNER_TEXT,
        "no access at all": _NO_ACCESS_TEXT,
        "the record id does not exist — still HTTP 200, NOT a 404": (
            _ABSENT_RECORD_TEXT
        ),
    }
    for meaning, text in outcomes.items():
        result, urls = await _run(_envelope({"Response": text}))

        assert "error" not in result, meaning
        assert result["normalized"] is True, meaning
        assert result["access_origin_source"] == "Response", meaning
        assert result["access_origin"] == text, f"prose not verbatim: {meaning}"
        assert result["raw_response"] == {"Response": text}, meaning
        assert "count" not in result, meaning
        # Nothing is derived from the text.
        for fabricated in ("has_access", "access", "granted", "exists"):
            assert fabricated not in result, (
                f"{fabricated} was inferred from prose: {meaning}"
            )
        # The caller is warned in-band as well as in the docstring.
        assert "does NOT mean" in result["message"], meaning
        assert urls == [_EXPECTED_URL], meaning


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,source,value",
    [
        pytest.param(
            {"AccessOrigin": "Owner"},
            "AccessOrigin",
            "Owner",
            id="differently-named-lone-scalar",
        ),
        pytest.param({"HasAccess": False}, "HasAccess", False, id="falsy-scalar"),
        pytest.param({"Response": ""}, "Response", "", id="empty-string-response"),
    ],
)
async def test_lone_scalar_fallback_keeps_working(
    body: Any, source: str, value: Any
) -> None:
    """Tier 2: a scalar under another name is still unambiguous.

    ``falsy-scalar`` and ``empty-string-response`` are the truthiness trap:
    membership must be an explicit scalar check, or ``False``, ``0`` and ``""``
    would fall through to the raw path as if nothing had been found.
    """
    result, urls = await _run(_envelope(body))

    assert result["normalized"] is True
    assert result["access_origin_source"] == source
    assert result["access_origin"] == value
    assert "count" not in result
    assert result["raw_response"] == body
    assert urls == [_EXPECTED_URL]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,reason",
    [
        pytest.param(
            {"AccessOrigin": "Owner", "Extra": "Team"},
            "two scalars and no Response — which one is the origin?",
            id="ambiguous-scalars",
        ),
        pytest.param(
            {"Response": None},
            "an explicit null is nothing to report, not an origin",
            id="null-response",
        ),
        pytest.param(
            {"Origins": [{"Origin": "Role"}]},
            "a collection: live traffic proves the answer is never list-shaped",
            id="collection-body",
        ),
        pytest.param("OK", "a bare string payload", id="scalar-body"),
        pytest.param(None, "an empty body that is not JSON at all", id="empty-body"),
    ],
)
async def test_unexpected_shapes_degrade_to_raw_pass_through(
    body: Any, reason: str
) -> None:
    """No answer is better than a fabricated one."""
    result, urls = await _run(_envelope(body))

    assert "error" not in result, reason
    assert result["normalized"] is False, reason
    for key in ("access_origin", "access_origin_source", "count"):
        assert key not in result, f"{key} was fabricated: {reason}"
    expected = (
        {k: v for k, v in body.items() if not k.startswith("@odata.")}
        if isinstance(body, dict)
        else body
    )
    assert result["raw_response"] == expected
    assert urls == [_EXPECTED_URL]


@pytest.mark.asyncio
async def test_http_errors_use_the_standard_error_envelope() -> None:
    """An unknown table name is the live-confirmed error path: HTTP 400 0x80041102."""
    result, _ = await _run(
        {
            "error": {
                "code": "0x80041102",
                "message": (
                    "The entity with a name = 'zz_no_such_table_zz' with "
                    "namemapping = 'Logical' was not found in the MetadataCache."
                ),
            }
        },
        status_code=400,
    )

    assert result["error"] is True
    assert "400" in result["message"]
    assert "MetadataCache" in result["message"]
    assert "access_origin" not in result
    assert "raw_response" not in result


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        pytest.param(
            {"logical_name": "account')/WhoAmI("},
            "the confirmed key-predicate breakout payload",
            id="logical-name-breakout",
        ),
        pytest.param(
            {"logical_name": "acc'ount"},
            "a bare single quote — the character that terminates an OData literal",
            id="logical-name-single-quote",
        ),
        pytest.param(
            {"logical_name": "account,contact"},
            "a comma would add a parameter to the function call",
            id="logical-name-comma",
        ),
        pytest.param({"logical_name": ""}, "an empty logical name", id="empty-name"),
        pytest.param(
            {"logical_name": "a" * 51},
            "Dataverse caps a logical name at 50 characters",
            id="over-long-name",
        ),
        pytest.param({"object_id": "not-a-guid"}, "a non-GUID object_id", id="bad-object-id"),
        pytest.param(
            {"principal_id": f"{_PRINCIPAL_ID})/WhoAmI("},
            "a GUID with a URL-breakout suffix — the pattern is the only defence",
            id="principal-id-breakout",
        ),
        pytest.param(
            {"object_id": _OBJECT_ID[:-1]}, "a truncated GUID", id="short-object-id"
        ),
        pytest.param({"unknown": 1}, "extra='forbid' on the input model", id="unknown-field"),
    ],
)
def test_input_model_rejects_bad_input(kwargs: dict, reason: str) -> None:
    """Bad input is refused at the model boundary, so no URL is ever built."""
    args = {
        "dataverse_url": _BASE_URL,
        "object_id": _OBJECT_ID,
        "logical_name": _LOGICAL_NAME,
        "principal_id": _PRINCIPAL_ID,
        **kwargs,
    }
    with pytest.raises(ValidationError):
        RetrieveAccessOriginInput(**args)


@pytest.mark.asyncio
async def test_injection_payloads_never_reach_a_url() -> None:
    """Belt and braces: the breakout value is rejected before any request is made.

    ``encode_odata_literal`` at the build site would neutralize it anyway (the
    quote is doubled and then percent-encoded), but the model refuses it first, so
    ``request_with_retry`` is never called at all.
    """
    called: list[str] = []

    async def _side_effect(_client, _method, url, **_kwargs):  # pragma: no cover
        called.append(url)
        raise AssertionError(f"a request was made with a rejected input: {url}")

    with patch(
        "dataverse_mcp.tools.security.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ):
        with pytest.raises(ValidationError):
            RetrieveAccessOriginInput(
                dataverse_url=_BASE_URL,
                object_id=_OBJECT_ID,
                logical_name="account')/WhoAmI(",
                principal_id=_PRINCIPAL_ID,
            )

    assert called == []
