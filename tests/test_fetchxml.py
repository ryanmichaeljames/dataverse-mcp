"""Unit tests for dataverse_execute_fetchxml (issue #93).

Coverage:
- ExecuteFetchXmlInput model validation: required fields, fetch_xml validator,
  extra field rejected, whitespace stripping.
- Happy path: records + paging cookie (→ has_more) + @odata.count → full response.
- No paging annotations → has_more=false, paging_cookie/total_record_count absent.
- @odata.count=-1 → total_record_count omitted.
- include_formatted_values=true → Prefer header is set.
- HTTP error path (400) → {"error": true, ...} response.
- URL contains 'fetchXml=' with encoded fetch.

Mocking strategy: patch build_headers to return a minimal dict and replace
app_ctx.http_client.request with an AsyncMock that returns a crafted httpx.Response,
following the pattern used in test_record_crud.py / test_solution_import_export.py.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import ExecuteFetchXmlInput
from dataverse_mcp.tools.tables import dataverse_execute_fetchxml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_ENTITY_SET = "accounts"
_FETCH_XML = '<fetch version="1.0"><entity name="account"><attribute name="name"/></entity></fetch>'

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
        request=httpx.Request("GET", _BASE_URL),
    )


def _mock_http_response(app_ctx: AppContext, response: httpx.Response) -> None:
    """Patch app_ctx.http_client.request to return *response*."""
    app_ctx.http_client.request = AsyncMock(return_value=response)


# ---------------------------------------------------------------------------
# Input model validation
# ---------------------------------------------------------------------------


class TestExecuteFetchXmlInput:
    """ExecuteFetchXmlInput model validation."""

    def test_valid_input(self):
        m = ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            fetch_xml=_FETCH_XML,
        )
        assert m.entity_set_name == _ENTITY_SET
        assert m.fetch_xml == _FETCH_XML
        assert m.include_formatted_values is False

    def test_whitespace_stripped_from_fetch_xml(self):
        padded = f"  {_FETCH_XML}  "
        m = ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            fetch_xml=padded,
        )
        assert m.fetch_xml == _FETCH_XML

    def test_empty_fetch_xml_rejected(self):
        with pytest.raises(ValidationError):
            ExecuteFetchXmlInput(
                dataverse_url=_BASE_URL,
                entity_set_name=_ENTITY_SET,
                fetch_xml="",
            )

    def test_whitespace_only_fetch_xml_rejected(self):
        with pytest.raises(ValidationError):
            ExecuteFetchXmlInput(
                dataverse_url=_BASE_URL,
                entity_set_name=_ENTITY_SET,
                fetch_xml="   ",
            )

    def test_fetch_xml_not_starting_with_fetch_rejected(self):
        with pytest.raises(ValidationError):
            ExecuteFetchXmlInput(
                dataverse_url=_BASE_URL,
                entity_set_name=_ENTITY_SET,
                fetch_xml="<query>...</query>",
            )

    def test_fetch_xml_case_insensitive_prefix_check(self):
        # <Fetch ...> (uppercase F) must also be accepted
        m = ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            fetch_xml='<Fetch version="1.0"><entity name="account"/></Fetch>',
        )
        assert m.fetch_xml.startswith("<Fetch")

    def test_missing_entity_set_name_rejected(self):
        with pytest.raises(ValidationError):
            ExecuteFetchXmlInput(
                dataverse_url=_BASE_URL,
                fetch_xml=_FETCH_XML,
            )

    def test_missing_fetch_xml_rejected(self):
        with pytest.raises(ValidationError):
            ExecuteFetchXmlInput(
                dataverse_url=_BASE_URL,
                entity_set_name=_ENTITY_SET,
            )

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ExecuteFetchXmlInput(
                dataverse_url=_BASE_URL,
                entity_set_name=_ENTITY_SET,
                fetch_xml=_FETCH_XML,
                unknown_field="should_fail",
            )

    def test_include_formatted_values_defaults_to_false(self):
        m = ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            fetch_xml=_FETCH_XML,
        )
        assert m.include_formatted_values is False

    def test_include_formatted_values_can_be_set_true(self):
        m = ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            fetch_xml=_FETCH_XML,
            include_formatted_values=True,
        )
        assert m.include_formatted_values is True


# ---------------------------------------------------------------------------
# Tool — happy path with full paging metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteFetchXmlHappyPath:
    """dataverse_execute_fetchxml returns expected response structure."""

    async def _call(
        self,
        response_body: dict,
        include_formatted_values: bool = False,
    ) -> tuple[dict, AppContext]:
        app_ctx = _make_app_ctx()
        ctx = _make_ctx(app_ctx)
        params = ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            fetch_xml=_FETCH_XML,
            include_formatted_values=include_formatted_values,
        )
        response = _make_response(200, response_body)
        _mock_http_response(app_ctx, response)

        with patch(
            "dataverse_mcp.tools.tables.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer tok"}),
        ):
            result = await dataverse_execute_fetchxml(params, ctx)
        return json.loads(result), app_ctx

    async def test_happy_path_full_paging_metadata(self):
        """Records + paging cookie (→ has_more) + @odata.count → full response."""
        body = {
            "@odata.context": "https://yourorg.crm.dynamics.com/api/data/v9.2/$metadata#accounts",
            "value": [{"accountid": "aaa", "name": "Contoso"}, {"accountid": "bbb", "name": "Fabrikam"}],
            "@odata.count": 42,
            "@Microsoft.Dynamics.CRM.fetchxmlpagingcookie": "<cookie page='2'/>",
        }
        data, _ = await self._call(body)

        assert data["count"] == 2
        assert len(data["records"]) == 2
        # has_more is driven by the presence of the paging cookie.
        assert data["has_more"] is True
        assert data["total_record_count"] == 42
        assert data["paging_cookie"] == "<cookie page='2'/>"
        assert "@odata.context" not in data

    async def test_url_contains_fetchxml_param(self):
        """GET URL must include fetchXml= with the encoded FetchXML."""
        body = {"value": []}
        _, app_ctx = await self._call(body)

        call_args = app_ctx.http_client.request.call_args
        called_url: str = call_args[1].get("url") or call_args[0][1]
        assert "fetchXml=" in called_url
        # The XML must be percent-encoded (no raw angle brackets)
        assert "<fetch" not in called_url
        assert "%3Cfetch" in called_url or "%3cfetch" in called_url

    async def test_no_paging_annotations_returns_defaults(self):
        """No paging annotations → has_more=False, paging_cookie/total_record_count absent."""
        body = {"value": [{"accountid": "aaa"}]}
        data, _ = await self._call(body)

        assert data["has_more"] is False
        assert "paging_cookie" not in data
        assert "total_record_count" not in data
        assert data["count"] == 1

    async def test_negative_totalrecordcount_omitted(self):
        """@odata.count=-1 (not computed) must be omitted from response."""
        body = {
            "value": [{"accountid": "aaa"}],
            "@odata.count": -1,
        }
        data, _ = await self._call(body)
        assert "total_record_count" not in data

    async def test_zero_totalrecordcount_included(self):
        """@odata.count=0 is a valid non-negative count and must be included."""
        body = {
            "value": [],
            "@odata.count": 0,
        }
        data, _ = await self._call(body)
        assert data["total_record_count"] == 0

    async def test_include_formatted_values_sets_prefer_header(self):
        """include_formatted_values=True causes the Prefer header to be set."""
        body = {"value": []}
        _captured_extra: list[dict] = []

        async def _fake_build_headers(app_ctx, base_url, *, extra=None, **kwargs):
            if extra:
                _captured_extra.append(extra)
            return {"Authorization": "Bearer tok"}

        app_ctx = _make_app_ctx()
        ctx = _make_ctx(app_ctx)
        params = ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            fetch_xml=_FETCH_XML,
            include_formatted_values=True,
        )
        response = _make_response(200, body)
        _mock_http_response(app_ctx, response)

        with patch(
            "dataverse_mcp.tools.tables.build_headers",
            new=_fake_build_headers,
        ):
            await dataverse_execute_fetchxml(params, ctx)

        assert _captured_extra, "Expected build_headers to be called with extra headers"
        prefer_value = _captured_extra[0].get("Prefer", "")
        assert "OData.Community.Display.V1.FormattedValue" in prefer_value

    async def test_include_formatted_values_false_no_prefer_header(self):
        """include_formatted_values=False (default) must NOT set the Prefer header."""
        body = {"value": []}
        _captured_extra: list = []

        async def _fake_build_headers(app_ctx, base_url, *, extra=None, **kwargs):
            _captured_extra.append(extra)
            return {"Authorization": "Bearer tok"}

        app_ctx = _make_app_ctx()
        ctx = _make_ctx(app_ctx)
        params = ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            fetch_xml=_FETCH_XML,
            include_formatted_values=False,
        )
        response = _make_response(200, body)
        _mock_http_response(app_ctx, response)

        with patch(
            "dataverse_mcp.tools.tables.build_headers",
            new=_fake_build_headers,
        ):
            await dataverse_execute_fetchxml(params, ctx)

        # extra should be None (empty dict evaluates falsy → None passed to build_headers)
        for extra in _captured_extra:
            assert extra is None or not extra.get("Prefer")

    async def test_per_record_etag_preserved(self):
        """@odata.etag on individual records must NOT be stripped."""
        body = {
            "value": [
                {"accountid": "aaa", "@odata.etag": 'W/"12345"'},
            ],
        }
        data, _ = await self._call(body)
        assert "@odata.etag" in data["records"][0]

    async def test_odata_context_stripped_from_top_level(self):
        """@odata.context at the top level must be stripped from the response."""
        body = {
            "@odata.context": "...$metadata#accounts",
            "value": [{"accountid": "aaa"}],
        }
        data, _ = await self._call(body)
        assert "@odata.context" not in data

    async def test_empty_records(self):
        """Empty value array → count=0, has_more=False."""
        body = {"value": []}
        data, _ = await self._call(body)
        assert data["count"] == 0
        assert data["records"] == []
        assert data["has_more"] is False


# ---------------------------------------------------------------------------
# Tool — HTTP error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteFetchXmlErrorPath:
    """dataverse_execute_fetchxml handles HTTP errors correctly."""

    async def test_http_400_returns_error_json(self):
        """A 400 response from Dataverse must return {"error": true, ...}."""
        app_ctx = _make_app_ctx()
        ctx = _make_ctx(app_ctx)
        params = ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            fetch_xml=_FETCH_XML,
        )
        error_body = {
            "error": {
                "code": "0x80060888",
                "message": "FetchXML is not valid",
            }
        }
        response = _make_response(400, error_body)
        app_ctx.http_client.request = AsyncMock(return_value=response)

        with patch(
            "dataverse_mcp.tools.tables.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer tok"}),
        ):
            result = await dataverse_execute_fetchxml(params, ctx)

        data = json.loads(result)
        assert data.get("error") is True
        assert "message" in data
