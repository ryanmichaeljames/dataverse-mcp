"""Unit tests for dataverse_get_import_job_results.

``RetrieveFormattedImportJobResults`` is an unbound GET taking one ``Edm.Guid``
parameter. Its return type ``RetrieveFormattedImportJobResultsResponse`` is
documented, but its INNER properties are NOT, so the tool locates the results
document by name then by shape, and degrades to raw pass-through when it cannot.
Live testing since confirmed the payload arrives under ``FormattedResults`` with
nothing else in the body, and that the document itself is a SpreadsheetML (Excel
XML) workbook — spreadsheet tags, meaning in the cell text — which is why the
summary stays descriptive and no tag name is ever read as a verdict. The fallback
tiers are kept because the property name is still not contractual.

Acceptance criteria:
- Happy path: the document is found, the ``@odata`` envelope is stripped, the text
  is passed through verbatim, and the Guid is written BARE in the URL (no quotes,
  no ``guid'...'`` prefix, nothing percent-encoded).
- Size: the text is trimmed to ``max_chars`` while ``results_length`` keeps the
  true full length and ``truncated`` says so; the structural summary is computed
  over the WHOLE document, not just the returned slice.
- The summary is descriptive only — tags are tallied, nothing is called an error.
- Malformed markup and an entity-expansion payload are handled, never raised: the
  parse is refused and the text still comes back.
- An unrecognized payload shape degrades to ``normalized: false`` with nothing
  fabricated.
- An invalid GUID is refused at the input model, so no URL is ever built.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import GetImportJobResultsInput
from dataverse_mcp.tools.solutions import dataverse_get_import_job_results

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"
_JOB_ID = "3f9c1a52-8d47-4b0e-9c31-5a6e2d7b4f18"

# The exact URL the tool is expected to build: one parameter alias holding a bare
# Edm.Guid literal.
_EXPECTED_URL = (
    f"{_API_ROOT}/RetrieveFormattedImportJobResults(ImportJobId=@jid)?@jid={_JOB_ID}"
)

_DOC = (
    "<importexportxml>"
    "<solutionManifests><solutionManifest><UniqueName>MySolution</UniqueName>"
    "</solutionManifest></solutionManifests>"
    '<result result="failure" errorcode="0x8004F037" errortext="dependency" />'
    '<result result="success" />'
    "</importexportxml>"
)

# A billion-laughs style entity-expansion payload. defusedxml must refuse it
# outright rather than expanding it.
_XXE_DOC = (
    '<?xml version="1.0"?>'
    "<!DOCTYPE lolz [<!ENTITY lol 'lol'>"
    "<!ENTITY lol2 '&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;'>"
    "<!ENTITY lol3 '&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;'>"
    "]><lolz>&lol3;</lolz>"
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
    body: Any,
    *,
    status_code: int = 200,
    **kwargs: Any,
) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = GetImportJobResultsInput(
        dataverse_url=_BASE_URL, import_job_id=_JOB_ID, **kwargs
    )

    async def _side_effect(_client, _method, url, **_kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(status_code, json=body, request=request)

    with patch(
        "dataverse_mcp.tools.solutions.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.solutions.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_get_import_job_results(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


@pytest.mark.asyncio
async def test_happy_path_passes_the_document_through_and_builds_a_bare_guid_url() -> None:
    """The document is returned verbatim; nothing in it is interpreted."""
    result, urls = await _run(_envelope({"FormattedResults": _DOC}))

    assert "error" not in result
    assert result["normalized"] is True
    assert result["results_source"] == "FormattedResults"
    assert result["results"] == _DOC, "the document must be passed through verbatim"
    assert result["results_length"] == len(_DOC)
    assert result["returned_length"] == len(_DOC)
    assert result["truncated"] is False
    assert result["import_job_id"] == _JOB_ID
    assert "@odata.context" not in json.dumps(result)
    # Descriptive structure only — no node is labelled an error or a failure.
    assert result["markup_parsed"] is True
    assert result["root_tag"] == "importexportxml"
    assert result["element_counts"]["result"] == 2
    assert result["element_count"] == sum(result["element_counts"].values())
    for invented in ("has_errors", "error_count", "succeeded", "failed"):
        assert invented not in result, f"{invented} is a verdict the schema cannot support"
    # The tally ships with the caveat that tag names are structure, not outcome:
    # the live document is a SpreadsheetML workbook whose meaning is in cell text.
    assert "element_counts_note" in result
    assert "succeed" in result["element_counts_note"]

    assert urls == [_EXPECTED_URL], "the tool is exactly one round trip"
    # An Edm.Guid literal is bare: no quotes, no guid'...' prefix, nothing encoded.
    assert f"@jid={_JOB_ID}" in urls[0]
    assert "'" not in urls[0]
    assert "guid" not in urls[0]
    assert "%" not in urls[0]


@pytest.mark.asyncio
async def test_a_large_document_is_trimmed_but_reports_its_true_size() -> None:
    """max_chars bounds the text; results_length and truncated keep the magnitude."""
    filler = "<line>x</line>" * 5000
    doc = f"<results>{filler}</results>"
    assert len(doc) > 20_000

    result, _ = await _run(_envelope({"FormattedResults": doc}))

    assert result["returned_length"] == 20_000, "the default max_chars was not applied"
    assert len(result["results"]) == 20_000
    assert result["results"] == doc[:20_000]
    assert result["results_length"] == len(doc)
    assert result["truncated"] is True
    assert str(len(doc)) in result["message"]
    # The summary spans the WHOLE document, not the 20,000-character slice: 5,000
    # <line> elements are counted even though the slice holds far fewer.
    assert result["element_counts"]["line"] == 5000
    assert result["root_tag"] == "results"


@pytest.mark.asyncio
async def test_raising_max_chars_returns_the_whole_document() -> None:
    """The caller can always get everything back."""
    doc = f"<results>{'<line>x</line>' * 5000}</results>"
    result, _ = await _run(_envelope({"FormattedResults": doc}), max_chars=1_000_000)

    assert result["truncated"] is False
    assert result["results"] == doc
    assert result["returned_length"] == result["results_length"] == len(doc)
    assert "message" not in result


@pytest.mark.asyncio
async def test_malformed_markup_is_reported_not_raised() -> None:
    """A non-XML document (HTML, prose) still comes back in full."""
    doc = "<html><body>Import failed<br>see above</body>"
    result, _ = await _run(_envelope({"FormattedResults": doc}))

    assert "error" not in result
    assert result["normalized"] is True
    assert result["markup_parsed"] is False
    assert "not well-formed" in result["markup_note"]
    assert result["results"] == doc, "the text is returned even when it will not parse"
    for key in ("root_tag", "element_counts", "element_count"):
        assert key not in result, f"{key} was fabricated for an unparseable document"


@pytest.mark.asyncio
async def test_entity_expansion_payload_is_refused_by_the_hardened_parser() -> None:
    """A billion-laughs document is never expanded — defusedxml refuses it.

    The document is server-sourced, but it is built from a solution zip the caller
    supplied, so it is not trusted input. The tool must not hang, and must still
    hand the text back for a human to read.
    """
    result, _ = await _run(_envelope({"FormattedResults": _XXE_DOC}))

    assert "error" not in result
    assert result["markup_parsed"] is False
    # The note names the construct actually refused. defusedxml's default is
    # forbid_dtd=False, so a bare <!DOCTYPE root SYSTEM "..."> parses fine — only
    # an ENTITY declaration or an external entity reference raises.
    assert "entity" in result["markup_note"].lower()
    assert "DTD" not in result["markup_note"], (
        "a plain DTD is not what was refused; the note must not claim it was"
    )
    assert result["results"] == _XXE_DOC
    assert result["results_length"] == len(_XXE_DOC)
    assert "element_counts" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,source",
    [
        pytest.param({"Response": _DOC}, "Response", id="known-alternate-name"),
        pytest.param({"Whatever": _DOC}, "Whatever", id="lone-string-under-any-name"),
        pytest.param(
            {"RetrieveFormattedImportJobResultsResponse": {"Results": _DOC}},
            "RetrieveFormattedImportJobResultsResponse.Results",
            id="nested-one-level-deep",
        ),
        pytest.param({"FormattedResults": ""}, "FormattedResults", id="empty-document"),
    ],
)
async def test_the_document_is_located_by_shape_when_the_name_differs(
    body: dict, source: str
) -> None:
    """The inner property name is undocumented, so name then shape are both tried."""
    result, _ = await _run(_envelope(body))

    assert result["normalized"] is True
    assert result["results_source"] == source
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_an_empty_document_is_an_answer_not_a_shape_mismatch() -> None:
    """"No formatted results" is reported as length 0, not as a failure to parse."""
    result, _ = await _run(_envelope({"FormattedResults": ""}))

    assert result["normalized"] is True
    assert result["results"] == ""
    assert result["results_length"] == 0
    assert result["truncated"] is False
    assert result["markup_parsed"] is False
    assert "empty" in result["markup_note"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,reason",
    [
        pytest.param(
            {"A": "<x/>", "B": "<y/>"},
            "two candidate strings at the top level — which one is the results?",
            id="ambiguous-top-level",
        ),
        pytest.param(
            {"Outer": {"A": "<x/>", "B": "<y/>"}},
            "two candidate strings one level down",
            id="ambiguous-nested",
        ),
        pytest.param(
            {"Count": 12, "IsReadOnly": False},
            "no string anywhere — the dictionary artifact shape",
            id="no-string",
        ),
        pytest.param(
            {"FormattedResults": ["<x/>"]},
            "a list is not the single markup string this function returns",
            id="list-payload",
        ),
        pytest.param(None, "an empty body that is not JSON at all", id="empty-body"),
    ],
)
async def test_unexpected_shapes_degrade_to_raw_pass_through(
    body: Any, reason: str
) -> None:
    """No length and no summary are better than fabricated ones."""
    result, urls = await _run(_envelope(body))

    assert "error" not in result, reason
    assert result["normalized"] is False, reason
    for key in (
        "results",
        "results_length",
        "returned_length",
        "truncated",
        "markup_parsed",
        "element_counts",
    ):
        assert key not in result, f"{key} was fabricated: {reason}"
    assert result["import_job_id"] == _JOB_ID
    expected = (
        {k: v for k, v in body.items() if not k.startswith("@odata.")}
        if isinstance(body, dict)
        else body
    )
    assert result["raw_response"] == expected
    assert urls == [_EXPECTED_URL]


@pytest.mark.asyncio
async def test_a_missing_import_job_returns_an_actionable_message() -> None:
    """A well-formed but unknown job id is a 404, handled like dataverse_get_import_job."""
    result, _ = await _run(
        {"error": {"code": "0x80040217", "message": "Does Not Exist"}},
        status_code=404,
    )

    assert result["error"] is True
    assert _JOB_ID in result["message"]
    assert "dataverse_list_import_jobs" in result["message"]
    assert "results" not in result


@pytest.mark.asyncio
async def test_http_errors_use_the_standard_error_envelope() -> None:
    """A server-side failure is an error envelope, not a partial success."""
    result, _ = await _run(
        {"error": {"code": "0x80040265", "message": "Import job data is corrupt"}},
        status_code=400,
    )

    assert result["error"] is True
    assert "400" in result["message"]
    assert "results" not in result
    assert "raw_response" not in result


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        pytest.param(
            {"import_job_id": "not-a-guid"}, "a non-GUID import_job_id", id="bad-guid"
        ),
        pytest.param(
            {"import_job_id": _JOB_ID[:-1]}, "a truncated GUID", id="short-guid"
        ),
        pytest.param(
            {"import_job_id": f"{_JOB_ID})/WhoAmI("},
            "a GUID with a URL-breakout suffix — the pattern is the only defence, "
            "because an Edm.Guid literal is written bare and is never escaped",
            id="guid-breakout",
        ),
        pytest.param(
            {"import_job_id": f"{_JOB_ID}'"},
            "a GUID with a trailing quote",
            id="guid-quote",
        ),
        pytest.param(
            {"import_job_id": _JOB_ID, "max_chars": 999},
            "max_chars below the floor",
            id="max-chars-too-low",
        ),
        pytest.param(
            {"import_job_id": _JOB_ID, "max_chars": 2_000_001},
            "max_chars above the cap",
            id="max-chars-too-high",
        ),
        pytest.param(
            {"import_job_id": _JOB_ID, "unknown": 1},
            "extra='forbid' on the input model",
            id="unknown-field",
        ),
    ],
)
def test_input_model_rejects_bad_input(kwargs: dict, reason: str) -> None:
    """Bad input is refused at the model boundary, so no URL is ever built."""
    with pytest.raises(ValidationError):
        GetImportJobResultsInput(dataverse_url=_BASE_URL, **kwargs)
