"""DataverseClient wrapper with authentication factory and lifecycle management."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from azure.identity import (
    AzureCliCredential,
    ClientSecretCredential,
    InteractiveBrowserCredential,
)
from PowerPlatform.Dataverse.client import DataverseClient

logger = logging.getLogger(__name__)

SUPPORTED_AUTH_TYPES = ("interactive", "client_secret", "azure_cli")


def _build_credential(auth_type: str):
    """Build an Azure TokenCredential based on the configured auth type.

    Args:
        auth_type: One of 'interactive', 'client_secret', or 'azure_cli'.

    Returns:
        A TokenCredential instance for authenticating with Dataverse.

    Raises:
        ValueError: If the auth type is not supported or required env vars are missing.
    """
    if auth_type == "interactive":
        logger.info("Using InteractiveBrowserCredential for authentication")
        return InteractiveBrowserCredential()

    if auth_type == "client_secret":
        tenant_id = os.environ.get("AZURE_TENANT_ID")
        client_id = os.environ.get("AZURE_CLIENT_ID")
        client_secret = os.environ.get("AZURE_CLIENT_SECRET")
        if not all([tenant_id, client_id, client_secret]):
            raise ValueError(
                "client_secret auth requires AZURE_TENANT_ID, "
                "AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET environment variables"
            )
        logger.info("Using ClientSecretCredential for authentication")
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )

    if auth_type == "azure_cli":
        logger.info("Using AzureCliCredential for authentication")
        return AzureCliCredential()

    raise ValueError(
        f"Unsupported DATAVERSE_AUTH_TYPE: '{auth_type}'. "
        f"Supported values: {', '.join(SUPPORTED_AUTH_TYPES)}"
    )


@dataclass
class AppContext:
    """Application context holding the initialized DataverseClient."""

    client: DataverseClient


@asynccontextmanager
async def dataverse_lifespan(server) -> AsyncIterator[AppContext]:
    """FastMCP lifespan that initializes and cleans up the DataverseClient.

    Reads configuration from environment variables:
    - DATAVERSE_URL: The Dataverse organization URL (required)
    - DATAVERSE_AUTH_TYPE: Authentication method (default: 'azure_cli')

    Yields:
        AppContext containing the initialized DataverseClient.
    """
    dataverse_url = os.environ.get("DATAVERSE_URL")
    if not dataverse_url:
        raise ValueError(
            "DATAVERSE_URL environment variable is required "
            "(e.g., 'https://yourorg.crm.dynamics.com')"
        )

    auth_type = os.environ.get("DATAVERSE_AUTH_TYPE", "azure_cli").lower().strip()
    logger.info(
        "Initializing DataverseClient for %s (auth: %s)", dataverse_url, auth_type
    )

    credential = _build_credential(auth_type)
    client = DataverseClient(dataverse_url, credential)

    try:
        logger.info("DataverseClient initialized successfully")
        yield AppContext(client=client)
    finally:
        logger.info("Shutting down DataverseClient")
        client.close()
