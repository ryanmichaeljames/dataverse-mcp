"""Integration test scaffolds for record CRUD tools.

Requires:
  DATAVERSE_INTEGRATION_URL   — base org URL (no trailing slash)
  DATAVERSE_INTEGRATION_TOKEN — bearer access token

Write tests additionally require: DATAVERSE_ALLOW_WRITE=true
Delete tests additionally require: DATAVERSE_ALLOW_DELETE=true

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
        and os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or DATAVERSE_ALLOW_WRITE=true not set; "
        "skipping create record integration test."
    ),
)
def test_create_record_integration_scaffold() -> None:
    """Design scaffold: create a record and verify the returned id.

    Gated behind DATAVERSE_ALLOW_WRITE=true. Implement by:
    1. Build AppContext with a real credential.
    2. Call dataverse_create_record with a test entity_set_name and data dict.
    3. Assert result["id"] is a non-empty GUID string.
    4. Clean up: delete the created record (requires DATAVERSE_ALLOW_DELETE=true).
    """
    pytest.skip("Create record integration scaffold — not yet implemented.")


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or DATAVERSE_ALLOW_WRITE=true not set; "
        "skipping update record integration test."
    ),
)
def test_update_record_integration_scaffold() -> None:
    """Design scaffold: update a record and verify updated=True.

    Gated behind DATAVERSE_ALLOW_WRITE=true. Implement by:
    1. Create a test record to update (or use a known stable test record id).
    2. Call dataverse_update_record with the record_id and a partial data dict.
    3. Assert result["updated"] is True and result["id"] matches.
    4. Verify the change via dataverse_get_record.
    5. Clean up the test record if created here.
    """
    pytest.skip("Update record integration scaffold — not yet implemented.")


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get("DATAVERSE_ALLOW_DELETE", "").lower() == "true"
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or DATAVERSE_ALLOW_DELETE=true not set; "
        "skipping delete record integration test."
    ),
)
def test_delete_record_integration_scaffold() -> None:
    """Design scaffold: delete a record and verify deleted=True.

    Gated behind DATAVERSE_ALLOW_DELETE=true. Implement by:
    1. Create a throwaway test record first (requires DATAVERSE_ALLOW_WRITE=true).
    2. Call dataverse_delete_record with the record_id.
    3. Assert result["deleted"] is True and result["id"] matches.
    4. Verify the record is gone via dataverse_get_record (expect 404 error response).
    """
    pytest.skip("Delete record integration scaffold — not yet implemented.")
