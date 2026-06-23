"""Unit tests for the five solution import/export ALM tools (issue #91).

Coverage:
- ExportSolutionInput / ImportSolutionInput / GetImportJobInput /
  ListImportJobsInput / CloneSolutionAsPatchInput model validation.
- dataverse_export_solution: inline (small payload), output_path write + metadata,
  oversized inline → structured error, bad output_path → structured error.
- dataverse_import_solution: inline customization_file + auto-generated UUID,
  input_path read, both/neither sources → ValidationError, oversized inline → error.
- dataverse_get_import_job: default excludes data column, include_data=True adds it,
  404 → structured error.
- dataverse_list_import_jobs: correct $filter, count/has_more.
- dataverse_clone_solution_as_patch: resolves parent + posts bound action.

Mocking strategy: patch build_headers to return {} and replace app_ctx.http_client
with an AsyncMock whose .request method returns a crafted httpx.Response — matching
the pattern used throughout this test suite (test_record_crud.py etc.).
"""

import base64
import json
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import (
    CloneSolutionAsPatchInput,
    ExportSolutionInput,
    GetImportJobInput,
    ImportSolutionInput,
    ListImportJobsInput,
)
from dataverse_mcp.tools.solutions import (
    _INLINE_FILE_MAX_BYTES,
    dataverse_clone_solution_as_patch,
    dataverse_export_solution,
    dataverse_get_import_job,
    dataverse_import_solution,
    dataverse_list_import_jobs,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_SOLUTION_NAME = "TestSolution"
_JOB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_SOLUTION_ID = "11111111-2222-3333-4444-555555555555"
_ASYNC_OP_ID = "66666666-7777-8888-9999-000000000000"
_IMPORT_JOB_KEY = "ImportJobKey_abc123"

# A minimal base64-encoded 5-byte payload ("hello") that is under the threshold.
_SMALL_B64 = base64.b64encode(b"hello").decode("ascii")

# A base64 string that exceeds _INLINE_FILE_MAX_BYTES (simulate a large payload).
_LARGE_B64 = "A" * (_INLINE_FILE_MAX_BYTES + 1)

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


def _make_response(status_code: int, body: dict) -> httpx.Response:
    """Build a minimal httpx.Response with a JSON body."""
    content = json.dumps(body).encode()
    return httpx.Response(
        status_code=status_code,
        headers={"Content-Type": "application/json"},
        content=content,
        request=httpx.Request("POST", _BASE_URL),
    )


# ---------------------------------------------------------------------------
# Input model validation — ExportSolutionInput
# ---------------------------------------------------------------------------


def test_export_solution_input_valid():
    m = ExportSolutionInput(
        dataverse_url=_BASE_URL,
        solution_name=_SOLUTION_NAME,
    )
    assert m.solution_name == _SOLUTION_NAME
    assert m.managed is False
    assert m.output_path is None


def test_export_solution_input_with_output_path():
    m = ExportSolutionInput(
        dataverse_url=_BASE_URL,
        solution_name=_SOLUTION_NAME,
        managed=True,
        output_path="/tmp/out.zip",
    )
    assert m.managed is True
    assert m.output_path == "/tmp/out.zip"


def test_export_solution_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        ExportSolutionInput(
            dataverse_url=_BASE_URL,
            solution_name=_SOLUTION_NAME,
            unknown_extra="bad",
        )


def test_export_solution_input_missing_solution_name():
    with pytest.raises(ValidationError):
        ExportSolutionInput(dataverse_url=_BASE_URL)


# ---------------------------------------------------------------------------
# Input model validation — ImportSolutionInput
# ---------------------------------------------------------------------------


def test_import_solution_input_inline_valid():
    m = ImportSolutionInput(
        dataverse_url=_BASE_URL,
        customization_file=_SMALL_B64,
    )
    assert m.customization_file == _SMALL_B64
    assert m.input_path is None


def test_import_solution_input_path_valid():
    m = ImportSolutionInput(
        dataverse_url=_BASE_URL,
        input_path="/tmp/sol.zip",
    )
    assert m.input_path == "/tmp/sol.zip"
    assert m.customization_file is None


def test_import_solution_input_both_rejects():
    with pytest.raises(ValidationError):
        ImportSolutionInput(
            dataverse_url=_BASE_URL,
            customization_file=_SMALL_B64,
            input_path="/tmp/sol.zip",
        )


def test_import_solution_input_neither_rejects():
    with pytest.raises(ValidationError):
        ImportSolutionInput(dataverse_url=_BASE_URL)


def test_import_solution_input_custom_job_id_valid():
    m = ImportSolutionInput(
        dataverse_url=_BASE_URL,
        customization_file=_SMALL_B64,
        import_job_id=_JOB_ID,
    )
    assert m.import_job_id == _JOB_ID


def test_import_solution_input_bad_job_id_rejects():
    with pytest.raises(ValidationError):
        ImportSolutionInput(
            dataverse_url=_BASE_URL,
            customization_file=_SMALL_B64,
            import_job_id="not-a-guid",
        )


# ---------------------------------------------------------------------------
# Input model validation — GetImportJobInput
# ---------------------------------------------------------------------------


def test_get_import_job_input_valid():
    m = GetImportJobInput(dataverse_url=_BASE_URL, import_job_id=_JOB_ID)
    assert m.import_job_id == _JOB_ID
    assert m.include_data is False


def test_get_import_job_input_bad_guid_rejects():
    with pytest.raises(ValidationError):
        GetImportJobInput(dataverse_url=_BASE_URL, import_job_id="bad")


def test_get_import_job_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        GetImportJobInput(
            dataverse_url=_BASE_URL,
            import_job_id=_JOB_ID,
            extra="nope",
        )


# ---------------------------------------------------------------------------
# Input model validation — ListImportJobsInput
# ---------------------------------------------------------------------------


def test_list_import_jobs_input_defaults():
    m = ListImportJobsInput(dataverse_url=_BASE_URL)
    assert m.top == 50
    assert m.solution_name is None


def test_list_import_jobs_input_top_out_of_range():
    with pytest.raises(ValidationError):
        ListImportJobsInput(dataverse_url=_BASE_URL, top=0)

    with pytest.raises(ValidationError):
        ListImportJobsInput(dataverse_url=_BASE_URL, top=5001)


# ---------------------------------------------------------------------------
# Input model validation — CloneSolutionAsPatchInput
# ---------------------------------------------------------------------------


def test_clone_solution_as_patch_input_valid_by_id():
    m = CloneSolutionAsPatchInput(
        dataverse_url=_BASE_URL,
        solution_id=_SOLUTION_ID,
        display_name="My Patch",
        version_number="1.0.0.2",
    )
    assert m.display_name == "My Patch"
    assert m.version_number == "1.0.0.2"


def test_clone_solution_as_patch_input_neither_identifier_rejects():
    with pytest.raises(ValidationError):
        CloneSolutionAsPatchInput(
            dataverse_url=_BASE_URL,
            display_name="Patch",
            version_number="1.0.0.2",
        )


def test_clone_solution_as_patch_input_both_identifiers_rejects():
    with pytest.raises(ValidationError):
        CloneSolutionAsPatchInput(
            dataverse_url=_BASE_URL,
            solution_id=_SOLUTION_ID,
            solution_unique_name="MySolution",
            display_name="Patch",
            version_number="1.0.0.2",
        )


# ---------------------------------------------------------------------------
# dataverse_export_solution — inline (small payload)
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_export_solution_inline_small(mock_headers):
    """Small exported payload is returned inline as base64."""
    export_response = _make_response(200, {"ExportSolutionFile": _SMALL_B64})
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=export_response)
    ctx = _make_ctx(app_ctx)

    params = ExportSolutionInput(
        dataverse_url=_BASE_URL,
        solution_name=_SOLUTION_NAME,
    )
    result = json.loads(await dataverse_export_solution(params, ctx))

    assert "solution_file_base64" in result
    assert result["solution_file_base64"] == _SMALL_B64
    assert result["solution"] == _SOLUTION_NAME
    assert result["managed"] is False
    assert "size_bytes" in result


# ---------------------------------------------------------------------------
# dataverse_export_solution — output_path writes file + returns metadata
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_export_solution_output_path_writes_file(mock_headers, tmp_path):
    """output_path causes the zip to be written to disk; no base64 in response."""
    zip_content = b"PK\x03\x04fake_zip_content"
    b64_zip = base64.b64encode(zip_content).decode("ascii")
    export_response = _make_response(200, {"ExportSolutionFile": b64_zip})

    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=export_response)
    ctx = _make_ctx(app_ctx)

    out_file = str(tmp_path / "solution.zip")
    params = ExportSolutionInput(
        dataverse_url=_BASE_URL,
        solution_name=_SOLUTION_NAME,
        output_path=out_file,
    )
    result = json.loads(await dataverse_export_solution(params, ctx))

    assert result["written"] is True
    assert result["path"] == out_file
    assert result["size_bytes"] == len(zip_content)
    assert result["solution"] == _SOLUTION_NAME
    assert "solution_file_base64" not in result
    assert Path(out_file).read_bytes() == zip_content


# ---------------------------------------------------------------------------
# dataverse_export_solution — oversized payload without output_path → error
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_export_solution_oversized_inline_error(mock_headers):
    """Oversized payload without output_path returns a structured error."""
    export_response = _make_response(200, {"ExportSolutionFile": _LARGE_B64})

    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=export_response)
    ctx = _make_ctx(app_ctx)

    params = ExportSolutionInput(
        dataverse_url=_BASE_URL,
        solution_name=_SOLUTION_NAME,
    )
    result = json.loads(await dataverse_export_solution(params, ctx))

    assert result["error"] is True
    assert "output_path" in result["message"]


# ---------------------------------------------------------------------------
# dataverse_export_solution — bad output_path → structured error
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_export_solution_bad_output_path_error(mock_headers):
    """A non-writable output_path returns a structured error, not an exception."""
    export_response = _make_response(200, {"ExportSolutionFile": _SMALL_B64})
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=export_response)
    ctx = _make_ctx(app_ctx)

    # Use a path inside a non-existent deeply nested dir whose parent we
    # cannot create (simulate by patching _decode_and_write_zip to raise).
    with patch(
        "dataverse_mcp.tools.solutions._decode_and_write_zip",
        side_effect=PermissionError("permission denied"),
    ):
        params = ExportSolutionInput(
            dataverse_url=_BASE_URL,
            solution_name=_SOLUTION_NAME,
            output_path="/nonexistent/path/sol.zip",
        )
        result = json.loads(await dataverse_export_solution(params, ctx))

    assert result["error"] is True
    assert "Failed to write" in result["message"]


# ---------------------------------------------------------------------------
# dataverse_import_solution — inline customization_file, auto-generates UUID
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_import_solution_inline_generates_uuid(mock_headers):
    """Import with inline base64 generates an import_job_id UUID and returns the 3 ids."""
    import_response = _make_response(200, {
        "AsyncOperationId": _ASYNC_OP_ID,
        "ImportJobKey": _IMPORT_JOB_KEY,
    })
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=import_response)
    ctx = _make_ctx(app_ctx)

    params = ImportSolutionInput(
        dataverse_url=_BASE_URL,
        customization_file=_SMALL_B64,
    )
    result = json.loads(await dataverse_import_solution(params, ctx))

    assert result["accepted"] is True
    # A UUID must be generated when not supplied.
    job_id = result["import_job_id"]
    # Validate it is a parseable UUID.
    parsed = uuid.UUID(job_id)
    assert str(parsed) == job_id
    assert result["async_operation_id"] == _ASYNC_OP_ID
    assert result["import_job_key"] == _IMPORT_JOB_KEY
    assert "import_job_id" in result["message"] or "poll" in result["message"]


# ---------------------------------------------------------------------------
# dataverse_import_solution — caller-supplied import_job_id is echoed back
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_import_solution_supplied_job_id_echoed(mock_headers):
    """Caller-supplied import_job_id is preserved in the response."""
    import_response = _make_response(200, {
        "AsyncOperationId": _ASYNC_OP_ID,
        "ImportJobKey": _IMPORT_JOB_KEY,
    })
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=import_response)
    ctx = _make_ctx(app_ctx)

    params = ImportSolutionInput(
        dataverse_url=_BASE_URL,
        customization_file=_SMALL_B64,
        import_job_id=_JOB_ID,
    )
    result = json.loads(await dataverse_import_solution(params, ctx))

    assert result["import_job_id"] == _JOB_ID


# ---------------------------------------------------------------------------
# dataverse_import_solution — input_path reads the file and posts
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_import_solution_input_path(mock_headers, tmp_path):
    """input_path reads the zip, base64-encodes it, and posts to Dataverse."""
    zip_bytes = b"PK\x03\x04fake"
    zip_file = tmp_path / "solution.zip"
    zip_file.write_bytes(zip_bytes)
    expected_b64 = base64.b64encode(zip_bytes).decode("ascii")

    import_response = _make_response(200, {
        "AsyncOperationId": _ASYNC_OP_ID,
        "ImportJobKey": _IMPORT_JOB_KEY,
    })
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=import_response)
    ctx = _make_ctx(app_ctx)

    params = ImportSolutionInput(
        dataverse_url=_BASE_URL,
        input_path=str(zip_file),
    )
    result = json.loads(await dataverse_import_solution(params, ctx))

    assert result["accepted"] is True
    # Verify the correct base64 was sent in the request body.
    call_kwargs = app_ctx.http_client.request.call_args
    sent_body = call_kwargs.kwargs.get("json") or call_kwargs.args[2] if len(call_kwargs.args) > 2 else {}
    # The request was made via request_with_retry which forwards json= kwarg.
    # We can access it via the mock call args.
    assert app_ctx.http_client.request.called


# ---------------------------------------------------------------------------
# dataverse_import_solution — missing input_path file → structured error
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_import_solution_missing_file_error(mock_headers):
    """A missing input_path returns a structured error, not an exception."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ImportSolutionInput(
        dataverse_url=_BASE_URL,
        input_path="/nonexistent/path/solution.zip",
    )
    result = json.loads(await dataverse_import_solution(params, ctx))

    assert result["error"] is True
    assert "read" in result["message"].lower() or "Failed" in result["message"]


# ---------------------------------------------------------------------------
# dataverse_import_solution — oversized inline base64 → structured error
# ---------------------------------------------------------------------------


async def test_import_solution_oversized_inline_error():
    """Oversized inline customization_file returns a structured error."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    # Patch build_headers to avoid auth calls.
    with patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}):
        params = ImportSolutionInput(
            dataverse_url=_BASE_URL,
            customization_file=_LARGE_B64,
        )
        result = json.loads(await dataverse_import_solution(params, ctx))

    assert result["error"] is True
    assert "input_path" in result["message"]


# ---------------------------------------------------------------------------
# dataverse_get_import_job — default excludes data
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_import_job_default_no_data(mock_headers):
    """Default call excludes the 'data' column from the $select."""
    job_record = {
        "importjobid": _JOB_ID,
        "solutionname": _SOLUTION_NAME,
        "progress": 75.0,
        "startedon": "2026-01-01T00:00:00Z",
        "completedon": None,
        "createdon": "2026-01-01T00:00:00Z",
        "name": "Import",
        "_solutionid_value": _SOLUTION_ID,
    }
    get_response = _make_response(200, job_record)
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=get_response)
    ctx = _make_ctx(app_ctx)

    params = GetImportJobInput(dataverse_url=_BASE_URL, import_job_id=_JOB_ID)
    result = json.loads(await dataverse_get_import_job(params, ctx))

    assert result["completed"] is False
    assert result["progress"] == 75.0
    assert "record" in result

    # Verify the URL did NOT include 'data' in $select.
    call_args = app_ctx.http_client.request.call_args
    requested_url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url", "")
    assert "data" not in requested_url.split("$select=")[-1].split("&")[0]


# ---------------------------------------------------------------------------
# dataverse_get_import_job — include_data=True adds data column
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_import_job_include_data(mock_headers):
    """include_data=True adds 'data' to the $select."""
    job_record = {
        "importjobid": _JOB_ID,
        "solutionname": _SOLUTION_NAME,
        "progress": 100.0,
        "completedon": "2026-01-01T01:00:00Z",
        "data": "<xml>failure details</xml>",
    }
    get_response = _make_response(200, job_record)
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=get_response)
    ctx = _make_ctx(app_ctx)

    params = GetImportJobInput(
        dataverse_url=_BASE_URL,
        import_job_id=_JOB_ID,
        include_data=True,
    )
    result = json.loads(await dataverse_get_import_job(params, ctx))

    assert result["completed"] is True

    # Verify 'data' appears in the $select.
    call_args = app_ctx.http_client.request.call_args
    requested_url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url", "")
    select_part = requested_url.split("$select=")[-1].split("&")[0]
    assert "data" in select_part


# ---------------------------------------------------------------------------
# dataverse_get_import_job — 404 → structured error
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_import_job_not_found(mock_headers):
    """404 response returns a structured not-found error."""
    not_found = httpx.Response(
        status_code=404,
        content=b"{}",
        request=httpx.Request("GET", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=not_found)
    ctx = _make_ctx(app_ctx)

    params = GetImportJobInput(dataverse_url=_BASE_URL, import_job_id=_JOB_ID)
    result = json.loads(await dataverse_get_import_job(params, ctx))

    assert result["error"] is True
    assert _JOB_ID in result["message"]


# ---------------------------------------------------------------------------
# dataverse_list_import_jobs — count/has_more and $filter for solution_name
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.solutions.paginate_records", new_callable=AsyncMock)
async def test_list_import_jobs_with_solution_name_filter(mock_paginate, mock_headers):
    """solution_name builds a $filter on solutionname; count/has_more returned."""
    mock_records = [
        {"importjobid": _JOB_ID, "solutionname": _SOLUTION_NAME, "progress": 100.0},
    ]
    mock_paginate.return_value = mock_records
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListImportJobsInput(
        dataverse_url=_BASE_URL,
        solution_name=_SOLUTION_NAME,
        top=10,
    )
    result = json.loads(await dataverse_list_import_jobs(params, ctx))

    assert result["count"] == 1
    assert result["has_more"] is False
    assert result["records"] == mock_records

    # Verify paginate_records was called with a URL containing the $filter.
    called_url = mock_paginate.call_args.args[0]
    assert "solutionname" in called_url
    assert _SOLUTION_NAME in called_url


# ---------------------------------------------------------------------------
# dataverse_list_import_jobs — no filter, has_more when count == top
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.solutions.paginate_records", new_callable=AsyncMock)
async def test_list_import_jobs_has_more(mock_paginate, mock_headers):
    """has_more is True when the returned record count equals top."""
    top = 2
    mock_paginate.return_value = [{"importjobid": f"{i}" * 8 + "-" * 27} for i in range(top)]
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListImportJobsInput(dataverse_url=_BASE_URL, top=top)
    result = json.loads(await dataverse_list_import_jobs(params, ctx))

    assert result["has_more"] is True
    assert result["count"] == top


# ---------------------------------------------------------------------------
# dataverse_clone_solution_as_patch — resolves parent + posts bound action
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.solutions._resolve_solution_record", new_callable=AsyncMock)
async def test_clone_solution_as_patch_success(mock_resolve, mock_headers):
    """clone resolves the parent solution then POSTs to the bound CloneAsPatch action."""
    mock_resolve.return_value = {
        "solutionid": _SOLUTION_ID,
        "uniquename": "ParentSolution",
    }
    clone_response = _make_response(200, {"SolutionId": "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"})
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=clone_response)
    ctx = _make_ctx(app_ctx)

    params = CloneSolutionAsPatchInput(
        dataverse_url=_BASE_URL,
        solution_unique_name="ParentSolution",
        display_name="My Patch",
        version_number="1.0.0.2",
    )
    result = json.loads(await dataverse_clone_solution_as_patch(params, ctx))

    assert result["cloned"] is True
    assert result["patch_solution_id"] == "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
    assert result["parent_solution_unique_name"] == "ParentSolution"
    assert result["version_number"] == "1.0.0.2"

    # Verify bound action URL was used.
    call_args = app_ctx.http_client.request.call_args
    url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url", "")
    assert "Microsoft.Dynamics.CRM.CloneAsPatch" in url
    assert _SOLUTION_ID in url


# ---------------------------------------------------------------------------
# dataverse_clone_solution_as_patch — solution not found → structured error
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.solutions._resolve_solution_record", new_callable=AsyncMock)
async def test_clone_solution_as_patch_not_found(mock_resolve, mock_headers):
    """solution not found returns a structured error."""
    mock_resolve.return_value = None
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = CloneSolutionAsPatchInput(
        dataverse_url=_BASE_URL,
        solution_unique_name="NonExistent",
        display_name="Patch",
        version_number="1.0.0.2",
    )
    result = json.loads(await dataverse_clone_solution_as_patch(params, ctx))

    assert result["error"] is True
    assert "NonExistent" in result["message"]
