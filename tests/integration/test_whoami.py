"""Read-only integration test: WhoAmI endpoint.

Requires:
  DATAVERSE_INTEGRATION_URL   — base org URL (no trailing slash)
  DATAVERSE_INTEGRATION_TOKEN — bearer access token

Both must be set; otherwise every test in this module is skipped automatically
by tests/integration/conftest.py.
"""

import os

import httpx
import pytest

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
def test_whoami_returns_user_id() -> None:
    """GET WhoAmI returns HTTP 200 and a response containing UserId.

    Uses a pre-acquired bearer token supplied via DATAVERSE_INTEGRATION_TOKEN
    so no credential flow is exercised during the test run.
    """
    base_url = os.environ[_INTEGRATION_URL_VAR].rstrip("/")
    token = os.environ.get(_INTEGRATION_TOKEN_VAR, "")

    url = f"{base_url}/api/data/v9.2/WhoAmI"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=headers)

    assert response.status_code == 200, (
        f"WhoAmI returned HTTP {response.status_code}: {response.text[:500]}"
    )
    body = response.json()
    assert "UserId" in body, f"Expected 'UserId' in WhoAmI response; got keys: {list(body.keys())}"


# ---------------------------------------------------------------------------
# Write / delete integration tests (design only — always skip without write env)
# ---------------------------------------------------------------------------
# These tests are intentionally gated so they NEVER run unless both
# DATAVERSE_ALLOW_WRITE=true and DATAVERSE_INTEGRATION_URL/TOKEN are set.
# They serve as a design scaffold for future CI expansion.


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or DATAVERSE_ALLOW_WRITE=true not set; "
        "skipping write integration test."
    ),
)
def test_write_integration_scaffold() -> None:
    """Design scaffold: create-then-teardown write integration test.

    Gated behind DATAVERSE_ALLOW_WRITE=true — skips in all default validation
    runs. Implement create → assert → delete fixture pattern here when needed.
    """
    pytest.skip("Write integration scaffold — not yet implemented.")


@pytest.mark.integration
@pytest.mark.skipif(
    not (
        os.environ.get(_INTEGRATION_URL_VAR)
        and os.environ.get("DATAVERSE_ALLOW_DELETE", "").lower() == "true"
    ),
    reason=(
        f"{_INTEGRATION_URL_VAR} or DATAVERSE_ALLOW_DELETE=true not set; "
        "skipping delete integration test."
    ),
)
def test_delete_integration_scaffold() -> None:
    """Design scaffold: create-then-delete integration test.

    Gated behind DATAVERSE_ALLOW_DELETE=true — skips in all default validation
    runs. Implement create → delete → assert-gone fixture pattern here when needed.
    """
    pytest.skip("Delete integration scaffold — not yet implemented.")
