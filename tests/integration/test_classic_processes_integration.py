"""Integration test scaffolds for classic process tools.

Requires:
  DATAVERSE_INTEGRATION_URL   — base org URL (no trailing slash)
  DATAVERSE_INTEGRATION_TOKEN — bearer access token

Write tests additionally require: DATAVERSE_ALLOW_WRITE=true

All tests are skipped unless the appropriate env vars are set, keeping the
default CI run secret-free.
"""

import os

import pytest

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get(_INTEGRATION_TOKEN_VAR)
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or {_INTEGRATION_TOKEN_VAR} not set; "
        "skipping list processes integration test."
    ),
)
def test_list_processes_integration_scaffold() -> None:
    """Design scaffold: list classic processes and verify response shape.

    Implement by:
    1. Build AppContext with a real credential from DATAVERSE_INTEGRATION_TOKEN.
    2. Call dataverse_list_processes with default params (type=1, no category).
    3. Assert result["count"] >= 0 and result["records"] is a list.
    4. Each record should have workflowid, name, category, statecode, statuscode.
    5. Verify category values are 0-4 (cloud flows at category=5 excluded by default).
    """
    pytest.skip("List processes integration scaffold — not yet implemented.")


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get(_INTEGRATION_TOKEN_VAR)
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or {_INTEGRATION_TOKEN_VAR} not set; "
        "skipping list business rules integration test."
    ),
)
def test_list_business_rules_integration_scaffold() -> None:
    """Design scaffold: list business rule processes (category=2) only.

    Implement by:
    1. Call dataverse_list_processes with category=2.
    2. Assert all returned records have category == 2.
    3. Assert result["count"] >= 0.
    """
    pytest.skip("List business rules integration scaffold — not yet implemented.")


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or DATAVERSE_ALLOW_WRITE=true not set; "
        "skipping activate process integration test."
    ),
)
def test_activate_process_integration_scaffold() -> None:
    """Design scaffold: activate a known draft classic process.

    Implement by:
    1. Build AppContext with a real credential.
    2. Identify a known draft workflow GUID in the sandbox.
    3. Call dataverse_activate_process with that process_id.
    4. Assert result["updated"] is True and result["statecode"] == 1.
    5. Re-read the record and verify statecode=1 / statuscode=2 in Dataverse.
    6. Deactivate to restore state.
    """
    pytest.skip("Activate process integration scaffold — not yet implemented.")


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or DATAVERSE_ALLOW_WRITE=true not set; "
        "skipping deactivate process integration test."
    ),
)
def test_deactivate_process_integration_scaffold() -> None:
    """Design scaffold: deactivate a known active classic process.

    Implement by:
    1. Build AppContext with a real credential.
    2. Identify a known active workflow GUID in the sandbox.
    3. Call dataverse_deactivate_process with that process_id.
    4. Assert result["updated"] is True and result["statecode"] == 0.
    5. Re-read the record and verify statecode=0 / statuscode=1 in Dataverse.
    6. Reactivate to restore state.
    """
    pytest.skip("Deactivate process integration scaffold — not yet implemented.")
