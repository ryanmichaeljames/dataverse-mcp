"""Integration test scaffolds for alternate key (EntityKeyMetadata) tools.

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
        and os.environ.get(_INTEGRATION_TOKEN_VAR)
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or {_INTEGRATION_TOKEN_VAR} not set; "
        "skipping list alternate keys integration test."
    ),
)
def test_list_alternate_keys_integration_scaffold() -> None:
    """Design scaffold: list alternate keys on a known table and verify response shape.

    Implement by:
    1. Build AppContext with a real credential from DATAVERSE_INTEGRATION_TOKEN.
    2. Call dataverse_list_alternate_keys with table_logical_name='account' (always exists).
    3. Assert result["count"] >= 0 and result["alternate_keys"] is a list.
    4. Each item should have SchemaName, LogicalName, KeyAttributes, EntityKeyIndexStatus.
    """
    pytest.skip("List alternate keys integration scaffold — not yet implemented.")


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or DATAVERSE_ALLOW_WRITE=true not set; "
        "skipping create alternate key integration test."
    ),
)
def test_create_alternate_key_integration_scaffold() -> None:
    """Design scaffold: create an alternate key and verify async index status is surfaced.

    Implement by:
    1. Build AppContext with a real credential.
    2. Choose a custom table and a unique string column in your sandbox.
    3. Call dataverse_create_alternate_key with a unique schema_name and that column.
    4. Assert result["created"] is True.
    5. Assert result["entity_key_index_status"] is not None (e.g., 'Pending' or 'Active').
    6. Assert result["note"] contains guidance about polling for 'Active' status.
    7. Clean up: call dataverse_delete_alternate_key (requires DATAVERSE_ALLOW_DELETE=true).
    """
    pytest.skip("Create alternate key integration scaffold — not yet implemented.")


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get("DATAVERSE_ALLOW_DELETE", "").lower() == "true"
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or DATAVERSE_ALLOW_DELETE=true not set; "
        "skipping delete alternate key integration test."
    ),
)
def test_delete_alternate_key_integration_scaffold() -> None:
    """Design scaffold: delete an alternate key and verify the response.

    Implement by:
    1. Build AppContext with a real credential.
    2. Create a test alternate key first (requires DATAVERSE_ALLOW_WRITE=true).
    3. Wait for EntityKeyIndexStatus='Active' via dataverse_list_alternate_keys.
    4. Call dataverse_delete_alternate_key with the key's logical name.
    5. Assert result["deleted"] is True.
    6. Verify the key is gone: call dataverse_list_alternate_keys and assert the key
       is no longer in the response.
    """
    pytest.skip("Delete alternate key integration scaffold — not yet implemented.")
