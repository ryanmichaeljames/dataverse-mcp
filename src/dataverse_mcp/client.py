"""DataverseClient wrapper with authentication factory and lifecycle management."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from urllib.parse import urlparse

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
    """Application context holding shared auth state and Dataverse clients."""

    credential: Any
    auth_type: str
    fallback_dataverse_url: str | None
    clients: dict[str, DataverseClient] = field(default_factory=dict)
    clients_lock: Lock = field(default_factory=Lock)


def normalize_dataverse_url(url: str) -> str:
    """Validate and normalize a Dataverse organization URL."""
    normalized_input = url.strip()
    if not normalized_input:
        raise ValueError("dataverse_url must not be empty")

    parsed = urlparse(normalized_input)
    if parsed.scheme.lower() != "https":
        raise ValueError(
            "dataverse_url must use https (e.g., 'https://yourorg.crm.dynamics.com')"
        )
    if parsed.username or parsed.password:
        raise ValueError("dataverse_url must not include credentials")
    if not parsed.hostname:
        raise ValueError(
            "dataverse_url must include a hostname "
            "(e.g., 'https://yourorg.crm.dynamics.com')"
        )
    if parsed.path not in ("", "/"):
        raise ValueError("dataverse_url must not include a path")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(
            "dataverse_url must not include params, query strings, or fragments"
        )

    netloc = parsed.hostname.lower()
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    return f"https://{netloc}"


def get_dataverse_client(
    app_ctx: AppContext, dataverse_url: str | None = None
) -> DataverseClient:
    """Resolve the effective Dataverse URL and return a cached client."""
    effective_url = dataverse_url or app_ctx.fallback_dataverse_url
    if not effective_url:
        raise ValueError(
            "No Dataverse environment was provided. Supply dataverse_url on the tool "
            "input, or set DATAVERSE_URL as a legacy fallback."
        )

    normalized_url = normalize_dataverse_url(effective_url)

    with app_ctx.clients_lock:
        client = app_ctx.clients.get(normalized_url)
        if client is None:
            logger.info(
                "Initializing DataverseClient for %s (auth: %s)",
                normalized_url,
                app_ctx.auth_type,
            )
            client = DataverseClient(normalized_url, app_ctx.credential)
            app_ctx.clients[normalized_url] = client

    return client


def get_bearer_token(app_ctx: AppContext, scope: str) -> str:
    """Acquire a bearer token from the shared credential for the given scope."""
    return app_ctx.credential.get_token(scope).token


@asynccontextmanager
async def dataverse_lifespan(server) -> AsyncIterator[AppContext]:
    """FastMCP lifespan that initializes shared auth state and client cache.

    Reads configuration from environment variables:
    - DATAVERSE_AUTH_TYPE: Authentication method (default: 'azure_cli')
    - DATAVERSE_URL: Optional legacy fallback when a tool omits dataverse_url

    Yields:
        AppContext containing shared auth state and cached Dataverse clients.
    """
    fallback_dataverse_url = os.environ.get("DATAVERSE_URL")
    if fallback_dataverse_url:
        fallback_dataverse_url = normalize_dataverse_url(fallback_dataverse_url)
        logger.info(
            "Configured legacy DATAVERSE_URL fallback for %s", fallback_dataverse_url
        )
    else:
        logger.info("No DATAVERSE_URL fallback configured; tools must provide dataverse_url")

    auth_type = os.environ.get("DATAVERSE_AUTH_TYPE", "azure_cli").lower().strip()
    logger.info("Initializing Dataverse credential (auth: %s)", auth_type)

    credential = _build_credential(auth_type)
    app_ctx = AppContext(
        credential=credential,
        auth_type=auth_type,
        fallback_dataverse_url=fallback_dataverse_url,
    )

    try:
        logger.info("Dataverse credential initialized successfully")
        yield app_ctx
    finally:
        logger.info("Shutting down Dataverse clients")
        with app_ctx.clients_lock:
            clients = list(app_ctx.clients.items())
            app_ctx.clients.clear()

        for dataverse_url, client in clients:
            try:
                client.close()
            except Exception:
                logger.exception("Failed to close DataverseClient for %s", dataverse_url)

        close_credential = getattr(credential, "close", None)
        if callable(close_credential):
            close_credential()
