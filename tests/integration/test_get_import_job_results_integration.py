"""Live integration coverage for dataverse_get_import_job_results.

``RetrieveFormattedImportJobResults`` is an unbound GET taking one ``Edm.Guid``
parameter. Microsoft Learn documents the call and the return TYPE
(``RetrieveFormattedImportJobResultsResponse``) but **not its inner properties**,
so — unlike the tools this suite sits beside — the tool's response shape is
EXPECTATION, NOT RECORD. This suite exists to turn it into a record.

WHAT A LIVE RUN MUST ESTABLISH (nothing below is verified yet):

* the top-level property name the document arrives under. The tool tries
  ``FormattedResults`` (the organization-service response class's property name),
  then ``Response``, then ``ImportJobResults``, then falls back to "exactly one
  string in the body". ``results_source`` in the tool's output names whichever
  fired — record it, and promote the winner in ``_IMPORT_JOB_RESULTS_KEYS``;
* whether the payload really is ONE string, or a structure;
* whether that string is XML, HTML or something else — ``markup_parsed`` and
  ``root_tag`` in the tool's output answer this. If it is XML, record ``root_tag``
  and the tag vocabulary in ``element_counts``: that is what a future summary,
  error-count or verdict feature would have to be built on, and it must not be
  invented ahead of this evidence;
* THE SIZE. ``max_chars`` defaults to 20,000 on the assumption that these
  documents are large. The raw-call test prints the true byte count — if the
  document is routinely tiny the default should come down, and if it is megabytes
  the docstring's "the reason is usually near the top" guidance needs checking.

No import job id is hardcoded: every id is discovered live from ``importjobs``.
A clean sandbox may legitimately have NO import jobs at all, in which case every
test SKIPS with a clear reason rather than failing.

Read-only throughout: nothing is imported, created, updated or published.

Requires (else auto-skipped by tests/integration/conftest.py):
  DATAVERSE_INTEGRATION_URL   — base org URL
  DATAVERSE_INTEGRATION_TOKEN — bearer access token for that org
"""

import json
import logging
import os
import re
import time
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from dataverse_mcp.client import _DATAVERSE_API_VERSION, AppContext, resolve_base_url
from dataverse_mcp.models import GetImportJobResultsInput
from dataverse_mcp.tools.solutions import dataverse_get_import_job_results

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# One narrow page of the most recent import jobs is enough to find a candidate.
_IMPORT_JOB_PAGE_SIZE = 25

_RAW_BODY_PREVIEW = 4000

_NO_JOBS_REASON = (
    "this org has no importjob rows — nothing has been imported into it, so there "
    "are no formatted import results to read. Import a solution and re-run."
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get(_INTEGRATION_URL_VAR),
        reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
    ),
]


@pytest.fixture(autouse=True)
def _quiet_httpx_request_logging():
    """Stop httpx's INFO logger printing the org host and job GUID unredacted.

    ``httpx._client`` logs every request as ``HTTP Request: GET <full url> ...``
    at INFO. Under pytest's log capture that lands in the console BELOW this
    module's ``_redact`` helper, leaking exactly what ``_redact`` exists to hide.
    Raising the logger's own level drops the records at source, so no handler or
    capture setting can resurrect them.
    """
    logger = logging.getLogger("httpx")
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(previous)


def _url() -> str:
    return os.environ[_INTEGRATION_URL_VAR]


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ[_INTEGRATION_TOKEN_VAR]}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }


def _redact(text: str) -> str:
    """Strip the org host and every GUID before anything is printed."""
    return _GUID_RE.sub(
        "<guid>", text.replace(resolve_base_url(_url()), "https://<org>")
    )


def _dump(label: str, payload: Any) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    print(f"\n=== {label} ({len(text)} bytes as printed) ===")
    print(_redact(text))


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """MCPServer-style ctx backed by an AppContext pre-seeded with the sandbox token."""
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _get(path: str) -> httpx.Response:
    base_url = resolve_base_url(_url())
    async with httpx.AsyncClient(timeout=60.0) as client:
        return await client.get(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}{path}", headers=_headers()
        )


async def _import_jobs() -> list[dict]:
    """Discover import jobs live — no id is ever hardcoded.

    The large ``data`` column is deliberately NOT selected: it is the opaque XML
    blob this tool exists to avoid, and it is declared with a ~1 GB max length.
    """
    response = await _get(
        "/importjobs?$select=importjobid,solutionname,progress,completedon,createdon"
        f"&$top={_IMPORT_JOB_PAGE_SIZE}&$orderby=createdon desc"
    )
    assert response.status_code == 200, (
        "import job discovery failed — HTTP "
        f"{response.status_code}: {_redact(response.text[:500])}"
    )
    rows = [
        row
        for row in response.json().get("value", [])
        if isinstance(row.get("importjobid"), str)
    ]
    print(f"\n=== discovered {len(rows)} import job(s) (ids redacted below) ===")
    for row in rows[:5]:
        print(
            f"  progress={row.get('progress')} "
            f"completedon={row.get('completedon')} "
            f"createdon={row.get('createdon')}"
        )
    return rows


async def _latest_job() -> dict | None:
    rows = await _import_jobs()
    return rows[0] if rows else None


async def _call(import_job_id: str, **kwargs: Any) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_get_import_job_results(
                GetImportJobResultsInput(
                    dataverse_url=_url(), import_job_id=import_job_id, **kwargs
                ),
                ctx,
            )
        )


async def test_raw_call_records_the_undocumented_response_shape_and_size() -> None:
    """Call the function with the exact URL the tool builds and RECORD the answer.

    This is the test that pins the undocumented payload down. It prints the raw
    body, its size, the top-level property names and their types — everything
    needed to correct the tool from the failure output alone.
    """
    job = await _latest_job()
    if job is None:
        pytest.skip(_NO_JOBS_REASON)

    base_url = resolve_base_url(_url())
    # Exactly the URL src/dataverse_mcp/tools/solutions.py builds: one parameter
    # alias holding a BARE Edm.Guid literal (no quotes, no guid'...' prefix).
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveFormattedImportJobResults(ImportJobId=@jid)"
        f"?@jid={job['importjobid']}"
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.get(url, headers=_headers())

    print("\n=== RetrieveFormattedImportJobResults raw HTTP call ===")
    print(f"url (org host + guid redacted): {_redact(url)}")
    print(f"status: {response.status_code}")
    print(f"RESPONSE SIZE: {len(response.content)} bytes")

    assert response.status_code == 200, (
        "the bare-Guid parameter-alias URL was rejected — "
        f"HTTP {response.status_code}: {_redact(response.text[:1000])}. If this is a "
        "404 for a job id that dataverse_list_import_jobs just returned, the "
        "function's parameter name or binding is not what Microsoft Learn documents "
        "— record the finding before changing the tool."
    )

    body = response.json()
    print(f"RAW BODY (first {_RAW_BODY_PREVIEW} chars):")
    print(_redact(response.text[:_RAW_BODY_PREVIEW]))

    top_level = sorted(k for k in body if not k.startswith("@odata."))
    print(f"RECORDED top_level_keys (envelope stripped): {top_level}")
    print(
        "RECORDED value_types: "
        + json.dumps({k: type(body[k]).__name__ for k in top_level}, sort_keys=True)
    )
    for key in top_level:
        value = body[key]
        if isinstance(value, str):
            print(f"RECORDED '{key}' is a string of {len(value)} characters")
            print(f"  first 500 chars: {_redact(value[:500])}")

    assert top_level, (
        "the response carried nothing but the @odata envelope — there is no results "
        "document to read; record the raw body printed above"
    )


async def test_the_tool_returns_the_document_and_reports_its_true_size() -> None:
    """The tool's own contract against a real import job."""
    job = await _latest_job()
    if job is None:
        pytest.skip(_NO_JOBS_REASON)

    result = await _call(job["importjobid"])
    serialized = json.dumps(result)
    print(f"\n=== TOOL RESPONSE SIZE {len(serialized)} bytes ===")
    # The document itself can be large, so the response is summarized rather than
    # dumped whole; the raw-call test above prints the source material.
    _dump(
        "tool response (results text elided)",
        {k: v for k, v in result.items() if k != "results"},
    )

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["import_job_id"] == job["importjobid"]
    assert result["normalized"] is True, (
        "no results document could be located — the response shape is not what the "
        "tool expects. raw_response is printed above: record the REAL property name "
        "and put it at the front of _IMPORT_JOB_RESULTS_KEYS in "
        "src/dataverse_mcp/tools/solutions.py"
    )
    assert isinstance(result["results"], str)
    assert result["returned_length"] == len(result["results"])
    assert result["results_length"] >= result["returned_length"]
    assert result["truncated"] is (result["results_length"] > result["returned_length"])

    print(
        f"RECORDED: results_source={result['results_source']!r}, "
        f"results_length={result['results_length']} chars, "
        f"truncated={result['truncated']}, "
        f"markup_parsed={result['markup_parsed']}, "
        f"root_tag={result.get('root_tag')!r}"
    )
    print(f"RECORDED element_counts: {result.get('element_counts')}")
    print(f"RECORDED first 1000 chars of the document:\n{_redact(result['results'][:1000])}")

    if not result["markup_parsed"]:
        print(
            "RECORDED: the document did NOT parse as XML — "
            f"{result.get('markup_note')}. It may be HTML or plain text; record "
            "this before anything is built on a structural summary."
        )


async def test_a_small_max_chars_trims_but_never_hides_the_true_size() -> None:
    """Trimming is the size mechanism — prove the caller can still see the whole size."""
    job = await _latest_job()
    if job is None:
        pytest.skip(_NO_JOBS_REASON)

    full = await _call(job["importjobid"], max_chars=2_000_000)
    if full.get("error") or not full.get("normalized"):
        pytest.skip("the results document could not be read; see the tests above")
    if full["results_length"] <= 1000:
        pytest.skip(
            f"this org's results document is only {full['results_length']} characters, "
            "which is below the minimum max_chars — nothing to trim. RECORD THIS: if "
            "these documents are routinely tiny, the 20,000 default is too high."
        )

    trimmed = await _call(job["importjobid"], max_chars=1000)
    _dump(
        "trimmed tool response (results text elided)",
        {k: v for k, v in trimmed.items() if k != "results"},
    )

    assert trimmed["returned_length"] == 1000
    assert trimmed["results"] == full["results"][:1000]
    assert trimmed["results_length"] == full["results_length"], (
        "results_length must report the FULL document length regardless of trimming"
    )
    assert trimmed["truncated"] is True
    assert "message" in trimmed
    # The structural summary spans the whole document, not the returned slice.
    if trimmed.get("markup_parsed"):
        assert trimmed["element_counts"] == full["element_counts"], (
            "the summary must be computed over the WHOLE document, not the slice"
        )
    print(
        f"RECORDED: full document {full['results_length']} chars; the trimmed call "
        f"returned {trimmed['returned_length']} and still reported the true size"
    )


async def test_unknown_import_job_id_returns_the_error_envelope() -> None:
    """A well-formed but nonexistent GUID must not look like an empty document."""
    result = await _call("00000000-0000-0000-0000-000000000001")
    _dump("nonexistent import job: full tool response", result)

    assert result.get("error") is True, (
        "a nonexistent import job id did not return an error. An empty results "
        "document and 'no such job' would then be indistinguishable — record the "
        "real behaviour before relaxing this."
    )
    assert isinstance(result["message"], str) and result["message"]
    assert "results" not in result
    assert "raw_response" not in result
    print(f"RECORDED unknown-job error message: {_redact(result['message'])}")
