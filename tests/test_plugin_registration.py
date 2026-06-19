"""Unit tests for plug-in registration tools and models.

Philosophy (per memory/testing-philosophy.md):
- No live HTTP calls; httpx mocked via unittest.mock AsyncMock/MagicMock.
- Tests earn their keep: cover model validation invariants and request-body
  construction for representative operations. No redundant per-field coverage.

Coverage:
  1. Input-model validation: GUID rejection, option-set enum bounds, ≥1-field
     update validators, exactly-one/valid-combination identifier validators.
  2. Request body / @odata.bind / $filter construction for representative
     create, update, delete, and list operations per entity group.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote_plus

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.models import (
    CreatePluginAssemblyInput,
    CreatePluginStepImageInput,
    CreatePluginStepInput,
    CreatePluginTypeInput,
    DeletePluginAssemblyInput,
    GetPluginAssemblyInput,
    GetSdkMessageFilterInput,
    GetSdkMessageInput,
    ListPluginAssembliesInput,
    ListPluginStepImagesInput,
    ListPluginStepsInput,
    ListPluginTypesInput,
    UpdatePluginAssemblyInput,
    UpdatePluginPackageInput,
    UpdatePluginStepImageInput,
    UpdatePluginStepInput,
    UpdatePluginTypeInput,
)
from dataverse_mcp.tools.plugin_registration import (
    dataverse_create_plugin_assembly,
    dataverse_create_plugin_step,
    dataverse_create_plugin_step_image,
    dataverse_create_plugin_type,
    dataverse_delete_plugin_assembly,
    dataverse_get_plugin_assembly,
    dataverse_list_plugin_assemblies,
    dataverse_list_plugin_step_images,
    dataverse_list_plugin_steps,
    dataverse_list_plugin_types,
    dataverse_update_plugin_step,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_VALID_GUID = "12345678-1234-1234-1234-123456789abc"
_VALID_GUID_2 = "abcdef01-abcd-abcd-abcd-abcdef012345"
_VALID_GUID_3 = "ffffffff-ffff-ffff-ffff-ffffffffffff"
_VALID_URL = "https://yourorg.crm.dynamics.com"


def _mock_response(
    status_code: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = httpx.Headers(headers or {})
    resp.json.return_value = json_body or {}
    resp.text = json.dumps(json_body or {})
    resp.raise_for_status = MagicMock()
    return resp


def _make_app_ctx(responses: list) -> MagicMock:
    """Build a minimal AppContext mock whose http_client returns the given responses."""
    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.request = AsyncMock(side_effect=responses)
    app_ctx = MagicMock()
    app_ctx.http_client = http_client
    app_ctx._token_cache = {}
    app_ctx._token_locks = {}
    return app_ctx


def _make_ctx(app_ctx) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


# ===========================================================================
# 1. Input-model validation
# ===========================================================================


class TestGuidValidation:
    """GUID fields must reject malformed values."""

    def test_get_plugin_assembly_rejects_bad_guid(self):
        with pytest.raises(ValidationError, match="Invalid GUID"):
            GetPluginAssemblyInput(assembly_id="not-a-guid", dataverse_url=_VALID_URL)

    def test_delete_plugin_assembly_rejects_bad_guid(self):
        with pytest.raises(ValidationError, match="Invalid GUID"):
            DeletePluginAssemblyInput(assembly_id="12345678-bad", dataverse_url=_VALID_URL)

    def test_list_plugin_assemblies_rejects_bad_package_guid(self):
        with pytest.raises(ValidationError, match="Invalid GUID"):
            ListPluginAssembliesInput(package_id="not-a-guid", dataverse_url=_VALID_URL)

    def test_create_plugin_step_rejects_bad_plugin_type_guid(self):
        with pytest.raises(ValidationError, match="Invalid GUID"):
            CreatePluginStepInput(
                name="Test",
                plugin_type_id="bad-guid",
                message_id=_VALID_GUID,
                stage=40,
                mode=0,
                dataverse_url=_VALID_URL,
            )

    def test_create_plugin_step_rejects_bad_filter_id(self):
        with pytest.raises(ValidationError, match="Invalid GUID"):
            CreatePluginStepInput(
                name="Test",
                plugin_type_id=_VALID_GUID,
                message_id=_VALID_GUID_2,
                stage=40,
                mode=0,
                filter_id="not-a-guid",
                dataverse_url=_VALID_URL,
            )

    def test_get_sdk_message_rejects_bad_message_id(self):
        with pytest.raises(ValidationError, match="Invalid GUID"):
            GetSdkMessageInput(message_id="bad-guid", dataverse_url=_VALID_URL)

    def test_valid_guid_accepted(self):
        m = GetPluginAssemblyInput(assembly_id=_VALID_GUID, dataverse_url=_VALID_URL)
        assert m.assembly_id == _VALID_GUID


class TestOptionSetValidation:
    """Enum-constrained fields must reject out-of-range values."""

    def test_isolation_mode_rejects_invalid(self):
        with pytest.raises(ValidationError, match="isolation_mode"):
            CreatePluginAssemblyInput(name="x", content="abc123", isolation_mode=99, dataverse_url=_VALID_URL)

    def test_isolation_mode_accepts_valid_values(self):
        for v in (1, 2, 3):
            m = CreatePluginAssemblyInput(name="x", content="abc123", isolation_mode=v, dataverse_url=_VALID_URL)
            assert m.isolation_mode == v

    def test_stage_rejects_invalid(self):
        with pytest.raises(ValidationError, match="stage"):
            CreatePluginStepInput(
                name="x",
                plugin_type_id=_VALID_GUID,
                message_id=_VALID_GUID_2,
                stage=99,
                mode=0,
                dataverse_url=_VALID_URL,
            )

    def test_stage_accepts_valid_values(self):
        for v in (10, 20, 40):
            m = CreatePluginStepInput(
                name="x",
                plugin_type_id=_VALID_GUID,
                message_id=_VALID_GUID_2,
                stage=v,
                mode=0,
                dataverse_url=_VALID_URL,
            )
            assert m.stage == v

    def test_mode_rejects_invalid(self):
        with pytest.raises(ValidationError, match="mode"):
            CreatePluginStepInput(
                name="x",
                plugin_type_id=_VALID_GUID,
                message_id=_VALID_GUID_2,
                stage=40,
                mode=5,
                dataverse_url=_VALID_URL,
            )

    def test_supported_deployment_rejects_invalid(self):
        with pytest.raises(ValidationError, match="supported_deployment"):
            CreatePluginStepInput(
                name="x",
                plugin_type_id=_VALID_GUID,
                message_id=_VALID_GUID_2,
                stage=40,
                mode=0,
                supported_deployment=9,
                dataverse_url=_VALID_URL,
            )

    def test_image_type_rejects_invalid(self):
        with pytest.raises(ValidationError, match="image_type"):
            CreatePluginStepImageInput(
                step_id=_VALID_GUID,
                image_type=5,
                entity_alias="PreImage",
                message_property_name="Target",
                dataverse_url=_VALID_URL,
            )

    def test_image_type_accepts_valid_values(self):
        for v in (0, 1, 2):
            m = CreatePluginStepImageInput(
                step_id=_VALID_GUID,
                image_type=v,
                entity_alias="PreImage",
                message_property_name="Target",
                dataverse_url=_VALID_URL,
            )
            assert m.image_type == v

    def test_update_step_state_rejects_invalid(self):
        with pytest.raises(ValidationError, match="state"):
            UpdatePluginStepInput(step_id=_VALID_GUID, state="active", dataverse_url=_VALID_URL)


class TestAtLeastOneFieldValidators:
    """Update models require ≥1 updatable field."""

    def test_update_assembly_requires_at_least_one_field(self):
        with pytest.raises(ValidationError, match="At least one updatable field"):
            UpdatePluginAssemblyInput(assembly_id=_VALID_GUID, dataverse_url=_VALID_URL)

    def test_update_assembly_accepts_single_field(self):
        m = UpdatePluginAssemblyInput(assembly_id=_VALID_GUID, description="new", dataverse_url=_VALID_URL)
        assert m.description == "new"

    def test_update_package_requires_at_least_one_field(self):
        with pytest.raises(ValidationError, match="At least one updatable field"):
            UpdatePluginPackageInput(package_id=_VALID_GUID, dataverse_url=_VALID_URL)

    def test_update_type_requires_at_least_one_field(self):
        with pytest.raises(ValidationError, match="At least one updatable field"):
            UpdatePluginTypeInput(plugin_type_id=_VALID_GUID, dataverse_url=_VALID_URL)

    def test_update_step_requires_at_least_one_field(self):
        with pytest.raises(ValidationError, match="At least one updatable field"):
            UpdatePluginStepInput(step_id=_VALID_GUID, dataverse_url=_VALID_URL)

    def test_update_step_image_requires_at_least_one_field(self):
        with pytest.raises(ValidationError, match="At least one updatable field"):
            UpdatePluginStepImageInput(image_id=_VALID_GUID, dataverse_url=_VALID_URL)

    def test_update_step_accepts_state_only(self):
        m = UpdatePluginStepInput(step_id=_VALID_GUID, state="disabled", dataverse_url=_VALID_URL)
        assert m.state == "disabled"


class TestIdentifierValidators:
    """GetSdkMessageInput and GetSdkMessageFilterInput enforce exactly-one / valid-combination rules."""

    # GetSdkMessageInput
    def test_sdk_message_requires_exactly_one_identifier(self):
        with pytest.raises(ValidationError, match="Either message_name or message_id"):
            GetSdkMessageInput(dataverse_url=_VALID_URL)

    def test_sdk_message_rejects_both_identifiers(self):
        with pytest.raises(ValidationError, match="not both"):
            GetSdkMessageInput(message_name="Create", message_id=_VALID_GUID, dataverse_url=_VALID_URL)

    def test_sdk_message_accepts_name_only(self):
        m = GetSdkMessageInput(message_name="Create", dataverse_url=_VALID_URL)
        assert m.message_name == "Create"

    def test_sdk_message_accepts_id_only(self):
        m = GetSdkMessageInput(message_id=_VALID_GUID, dataverse_url=_VALID_URL)
        assert m.message_id == _VALID_GUID

    # GetSdkMessageFilterInput
    def test_filter_requires_identifier(self):
        with pytest.raises(ValidationError, match="Provide either filter_id"):
            GetSdkMessageFilterInput(dataverse_url=_VALID_URL)

    def test_filter_rejects_both_modes(self):
        with pytest.raises(ValidationError, match="not both modes"):
            GetSdkMessageFilterInput(
                filter_id=_VALID_GUID,
                message_id=_VALID_GUID_2,
                primary_entity="contact",
                dataverse_url=_VALID_URL,
            )

    def test_filter_accepts_filter_id_alone(self):
        m = GetSdkMessageFilterInput(filter_id=_VALID_GUID, dataverse_url=_VALID_URL)
        assert m.filter_id == _VALID_GUID

    def test_filter_accepts_message_plus_entity(self):
        m = GetSdkMessageFilterInput(message_id=_VALID_GUID, primary_entity="contact", dataverse_url=_VALID_URL)
        assert m.message_id == _VALID_GUID
        assert m.primary_entity == "contact"

    def test_filter_rejects_message_id_without_primary_entity(self):
        with pytest.raises(ValidationError, match="Both message_id and primary_entity"):
            GetSdkMessageFilterInput(message_id=_VALID_GUID, dataverse_url=_VALID_URL)

    def test_filter_rejects_primary_entity_without_message_id(self):
        with pytest.raises(ValidationError, match="Both message_id and primary_entity"):
            GetSdkMessageFilterInput(primary_entity="contact", dataverse_url=_VALID_URL)


# ===========================================================================
# 2. Request body / @odata.bind / $filter construction
# ===========================================================================


@pytest.mark.asyncio
class TestCreatePluginAssemblyBody:
    """dataverse_create_plugin_assembly must build the correct POST body."""

    async def test_required_fields_in_body(self):
        app_ctx = _make_app_ctx([
            _mock_response(
                204,
                headers={"OData-EntityId": "https://org/api/data/v9.2/pluginassemblies(abc)"},
            )
        ])
        ctx = _make_ctx(app_ctx)

        params = CreatePluginAssemblyInput(
            name="MyOrg.Plugins",
            content="base64content==",
            isolation_mode=2,
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={"Authorization": "Bearer tok"},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            result = await dataverse_create_plugin_assembly(params, ctx)

        data = json.loads(result)
        assert data["created"] is True
        assert data["name"] == "MyOrg.Plugins"

        # Inspect the actual body sent
        call_kwargs = app_ctx.http_client.request.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs.args[4]  # positional fallback
        assert sent_json["name"] == "MyOrg.Plugins"
        assert sent_json["content"] == "base64content=="
        assert sent_json["isolationmode"] == 2
        assert sent_json["sourcetype"] == 0  # fixed

    async def test_optional_fields_included_when_provided(self):
        app_ctx = _make_app_ctx([_mock_response(204)])
        ctx = _make_ctx(app_ctx)

        params = CreatePluginAssemblyInput(
            name="MyOrg.Plugins",
            content="base64content==",
            version="1.0.0.0",
            culture="neutral",
            public_key_token="abc123",
            description="My plugin",
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            await dataverse_create_plugin_assembly(params, ctx)

        call_kwargs = app_ctx.http_client.request.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs.args[4]
        assert sent_json["version"] == "1.0.0.0"
        assert sent_json["culture"] == "neutral"
        assert sent_json["publickeytoken"] == "abc123"
        assert sent_json["description"] == "My plugin"


@pytest.mark.asyncio
class TestCreatePluginStepBody:
    """dataverse_create_plugin_step must build correct @odata.bind navigation properties."""

    async def test_odata_bind_properties(self):
        app_ctx = _make_app_ctx([_mock_response(204)])
        ctx = _make_ctx(app_ctx)

        params = CreatePluginStepInput(
            name="MyOrg.Plugins.ContactPlugin: Create of contact",
            plugin_type_id=_VALID_GUID,
            message_id=_VALID_GUID_2,
            stage=40,
            mode=0,
            rank=1,
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            result = await dataverse_create_plugin_step(params, ctx)

        data = json.loads(result)
        assert data["created"] is True

        call_kwargs = app_ctx.http_client.request.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs.args[4]
        assert sent_json["eventhandler_plugintype@odata.bind"] == f"/plugintypes({_VALID_GUID})"
        assert sent_json["sdkmessageid@odata.bind"] == f"/sdkmessages({_VALID_GUID_2})"
        assert "sdkmessagefilterid@odata.bind" not in sent_json  # filter_id omitted

    async def test_filter_bind_included_when_provided(self):
        app_ctx = _make_app_ctx([_mock_response(204)])
        ctx = _make_ctx(app_ctx)

        params = CreatePluginStepInput(
            name="Test step",
            plugin_type_id=_VALID_GUID,
            message_id=_VALID_GUID_2,
            filter_id=_VALID_GUID_3,
            stage=20,
            mode=0,
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            await dataverse_create_plugin_step(params, ctx)

        call_kwargs = app_ctx.http_client.request.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs.args[4]
        assert sent_json["sdkmessagefilterid@odata.bind"] == f"/sdkmessagefilters({_VALID_GUID_3})"

    async def test_state_disabled_maps_to_statecode(self):
        app_ctx = _make_app_ctx([_mock_response(204)])
        ctx = _make_ctx(app_ctx)

        params = UpdatePluginStepInput(
            step_id=_VALID_GUID,
            state="disabled",
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            result = await dataverse_update_plugin_step(params, ctx)

        data = json.loads(result)
        assert data["updated"] is True

        call_kwargs = app_ctx.http_client.request.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs.args[4]
        assert sent_json["statecode"] == 1
        assert sent_json["statuscode"] == 2

    async def test_state_enabled_maps_to_statecode(self):
        app_ctx = _make_app_ctx([_mock_response(204)])
        ctx = _make_ctx(app_ctx)

        params = UpdatePluginStepInput(
            step_id=_VALID_GUID,
            state="enabled",
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            await dataverse_update_plugin_step(params, ctx)

        call_kwargs = app_ctx.http_client.request.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs.args[4]
        assert sent_json["statecode"] == 0
        assert sent_json["statuscode"] == 1


@pytest.mark.asyncio
class TestCreatePluginTypeBody:
    """dataverse_create_plugin_type must include the pluginassemblyid@odata.bind."""

    async def test_assembly_bind_in_body(self):
        app_ctx = _make_app_ctx([_mock_response(204)])
        ctx = _make_ctx(app_ctx)

        params = CreatePluginTypeInput(
            assembly_id=_VALID_GUID,
            typename="MyOrg.Plugins.ContactPlugin",
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            result = await dataverse_create_plugin_type(params, ctx)

        data = json.loads(result)
        assert data["created"] is True

        call_kwargs = app_ctx.http_client.request.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs.args[4]
        assert sent_json["pluginassemblyid@odata.bind"] == f"/pluginassemblies({_VALID_GUID})"
        assert sent_json["typename"] == "MyOrg.Plugins.ContactPlugin"


@pytest.mark.asyncio
class TestCreatePluginStepImageBody:
    """dataverse_create_plugin_step_image must include sdkmessageprocessingstepid@odata.bind."""

    async def test_step_bind_in_body(self):
        app_ctx = _make_app_ctx([_mock_response(204)])
        ctx = _make_ctx(app_ctx)

        params = CreatePluginStepImageInput(
            step_id=_VALID_GUID,
            image_type=1,
            entity_alias="PostImage",
            message_property_name="Target",
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            result = await dataverse_create_plugin_step_image(params, ctx)

        data = json.loads(result)
        assert data["created"] is True
        assert data["entity_alias"] == "PostImage"
        assert data["image_type"] == 1

        call_kwargs = app_ctx.http_client.request.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs.args[4]
        assert sent_json["sdkmessageprocessingstepid@odata.bind"] == (
            f"/sdkmessageprocessingsteps({_VALID_GUID})"
        )
        assert sent_json["imagetype"] == 1
        assert sent_json["entityalias"] == "PostImage"
        assert sent_json["messagepropertyname"] == "Target"


@pytest.mark.asyncio
class TestListFilters:
    """List tools must construct the correct OData $filter from input parameters."""

    async def test_list_plugin_assemblies_name_filter(self):
        app_ctx = _make_app_ctx([
            _mock_response(200, json_body={"value": []})
        ])
        ctx = _make_ctx(app_ctx)

        params = ListPluginAssembliesInput(
            name_contains="MyOrg",
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            result = await dataverse_list_plugin_assemblies(params, ctx)

        data = json.loads(result)
        assert data["count"] == 0
        # Verify the URL contained the filter (URL-decode before asserting)
        call_url = unquote_plus(app_ctx.http_client.request.call_args.args[1])
        assert "contains(name,'MyOrg')" in call_url

    async def test_list_plugin_types_assembly_filter(self):
        app_ctx = _make_app_ctx([
            _mock_response(200, json_body={"value": []})
        ])
        ctx = _make_ctx(app_ctx)

        params = ListPluginTypesInput(
            assembly_id=_VALID_GUID,
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            await dataverse_list_plugin_types(params, ctx)

        call_url = unquote_plus(app_ctx.http_client.request.call_args.args[1])
        assert f"_pluginassemblyid_value eq {_VALID_GUID}" in call_url

    async def test_list_plugin_steps_eventhandler_filter(self):
        app_ctx = _make_app_ctx([
            _mock_response(200, json_body={"value": []})
        ])
        ctx = _make_ctx(app_ctx)

        params = ListPluginStepsInput(
            plugin_type_id=_VALID_GUID,
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            await dataverse_list_plugin_steps(params, ctx)

        call_url = unquote_plus(app_ctx.http_client.request.call_args.args[1])
        assert f"_eventhandler_value eq {_VALID_GUID}" in call_url

    async def test_list_step_images_step_filter(self):
        app_ctx = _make_app_ctx([
            _mock_response(200, json_body={"value": []})
        ])
        ctx = _make_ctx(app_ctx)

        params = ListPluginStepImagesInput(
            step_id=_VALID_GUID,
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            await dataverse_list_plugin_step_images(params, ctx)

        call_url = unquote_plus(app_ctx.http_client.request.call_args.args[1])
        assert f"_sdkmessageprocessingstepid_value eq {_VALID_GUID}" in call_url


@pytest.mark.asyncio
class TestDeleteNotFound:
    """Delete tools must return a friendly error on 404, not an exception."""

    async def test_delete_assembly_404(self):
        app_ctx = _make_app_ctx([_mock_response(404)])
        ctx = _make_ctx(app_ctx)

        params = DeletePluginAssemblyInput(
            assembly_id=_VALID_GUID,
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            result = await dataverse_delete_plugin_assembly(params, ctx)

        data = json.loads(result)
        assert data["error"] is True
        assert "not found" in data["message"].lower()


@pytest.mark.asyncio
class TestGetRecord:
    """Get tools must strip @odata.context and wrap in {record: ...}."""

    async def test_get_plugin_assembly_response_shape(self):
        record = {
            "@odata.context": "https://org/api/data/v9.2/$metadata#pluginassemblies/$entity",
            "pluginassemblyid": _VALID_GUID,
            "name": "MyOrg.Plugins",
        }
        app_ctx = _make_app_ctx([_mock_response(200, json_body=record)])
        ctx = _make_ctx(app_ctx)

        params = GetPluginAssemblyInput(
            assembly_id=_VALID_GUID,
            dataverse_url="https://yourorg.crm.dynamics.com",
        )

        with (
            patch(
                "dataverse_mcp.tools.plugin_registration.build_headers",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "dataverse_mcp.tools.plugin_registration.resolve_base_url",
                return_value="https://yourorg.crm.dynamics.com",
            ),
        ):
            result = await dataverse_get_plugin_assembly(params, ctx)

        data = json.loads(result)
        assert "record" in data
        assert data["record"]["pluginassemblyid"] == _VALID_GUID
        assert "@odata.context" not in data["record"]
