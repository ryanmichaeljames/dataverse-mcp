"""Environment discovery tools for the Power Platform admin API."""

import asyncio
import json
import logging
from typing import Any

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import mcp
from dataverse_mcp.client import AppContext, get_bearer_token
from dataverse_mcp.models import ListEnvironmentsInput

logger = logging.getLogger(__name__)

_ENVIRONMENTS_ENDPOINT = (
    "https://api.bap.microsoft.com/providers/"
    "Microsoft.BusinessAppPlatform/scopes/admin/environments"
)
_ENVIRONMENTS_SCOPE = "https://service.powerapps.com/.default"
_ENVIRONMENTS_API_VERSION = "2020-10-01"


def _normalize_environment(raw_environment: dict[str, Any]) -> dict[str, Any]:
    """Normalize the raw Power Platform admin response into an agent-friendly shape."""
    properties = raw_environment.get("properties", {})
    linked = properties.get("linkedEnvironmentMetadata", {})
    states = properties.get("states", {})

    return {
        "environment_id": raw_environment.get("name") or linked.get("resourceId"),
        "display_name": properties.get("displayName") or linked.get("friendlyName"),
        "location": raw_environment.get("location"),
        "environment_sku": properties.get("environmentSku"),
        "is_default": properties.get("isDefault"),
        "instance_url": linked.get("instanceUrl"),
        "instance_api_url": linked.get("instanceApiUrl"),
        "unique_name": linked.get("uniqueName"),
        "domain_name": linked.get("domainName"),
        "management_state": states.get("management", {}).get("id"),
        "runtime_state": states.get("runtime", {}).get("id"),
        "azure_region": properties.get("azureRegion"),
        "created_time": properties.get("createdTime"),
        "description": properties.get("description"),
        "properties": properties,
    }


@mcp.tool(
    name="dataverse_list_environments",
    annotations={
        "title": "List Environments",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_environments(
    params: ListEnvironmentsInput, ctx: Context
) -> str:
    """List Power Platform environments available to the authenticated user.

    This tool uses the Power Platform admin API and does not require a
    dataverse_url. Use it to discover available environments before calling
    environment-specific Dataverse tools.
    """
    app_ctx: AppContext = ctx.request_context.lifespan_context

    try:
        bearer_token = await asyncio.to_thread(
            get_bearer_token,
            app_ctx,
            _ENVIRONMENTS_SCOPE,
        )

        expand_values: list[str] = []
        if params.expand_capacity:
            expand_values.append("properties.capacity")
        if params.expand_addons:
            expand_values.append("properties.addons")

        query_params: dict[str, Any] = {"api-version": _ENVIRONMENTS_API_VERSION}
        if expand_values:
            query_params["$expand"] = ",".join(expand_values)

        def _query():
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    _ENVIRONMENTS_ENDPOINT,
                    params=query_params,
                    headers={
                        "Authorization": f"Bearer {bearer_token}",
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                return response.json()

        payload = await asyncio.to_thread(_query)
        environments = [
            _normalize_environment(raw_environment)
            for raw_environment in payload.get("value", [])
        ]

        return json.dumps({
            "environments": environments,
            "count": len(environments),
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Power Platform admin API error: %s (status=%d)",
            e.response.text,
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": (
                "Power Platform admin API returned HTTP "
                f"{e.response.status_code}: {e.response.text}"
            ),
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_list_environments")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })