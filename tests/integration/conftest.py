"""Integration test configuration.

Integration tests require two environment variables to be set:
  DATAVERSE_INTEGRATION_URL   — base org URL (e.g. https://yourorg.crm.dynamics.com)
  DATAVERSE_INTEGRATION_TOKEN — a valid bearer access token for that org

When either variable is unset every integration test is automatically skipped,
keeping the default CI run secret-free.

Write/delete integration tests additionally gate on:
  DATAVERSE_ALLOW_WRITE   = "true"
  DATAVERSE_ALLOW_DELETE  = "true"
"""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-skip integration tests when the sandbox env vars are unset."""
    integration_url = os.environ.get("DATAVERSE_INTEGRATION_URL")
    integration_token = os.environ.get("DATAVERSE_INTEGRATION_TOKEN")

    if integration_url and integration_token:
        # Both present — integration tests may run; no modification needed.
        return

    skip_marker = pytest.mark.skip(
        reason=(
            "Integration tests require DATAVERSE_INTEGRATION_URL and "
            "DATAVERSE_INTEGRATION_TOKEN to be set."
        )
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)
