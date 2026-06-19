"""Unit tests for bounded paging on the four list tools: views, forms, apps, tables.

Acceptance criteria (issue #60):
- All four input models reject top < 1 and top > 5000, and default to 50.
- Each tool passes params.top to paginate_records and returns has_more = True
  when the result set is at or above the limit, False when below.
- count is always equal to the number of records returned.
- Both URL branches in dataverse_list_apps (published + RetrieveUnpublishedMultiple)
  honour top and produce the same has_more semantics.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.models import (
    ListAppsInput,
    ListFormsInput,
    ListTablesInput,
    ListViewsInput,
)
from dataverse_mcp.tools.apps import dataverse_list_apps
from dataverse_mcp.tools.forms import dataverse_list_forms
from dataverse_mcp.tools.metadata import dataverse_list_tables
from dataverse_mcp.tools.views import dataverse_list_views

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_app_ctx() -> MagicMock:
    """Return a minimal AppContext mock."""
    app_ctx = MagicMock()
    app_ctx.http_client = MagicMock(spec=httpx.AsyncClient)
    return app_ctx


def _make_ctx(app_ctx: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _fake_records(n: int) -> list[dict]:
    """Return n minimal record dicts."""
    return [{"id": str(i)} for i in range(n)]


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestTopFieldValidation:
    """top field must default to 50, reject < 1 and > 5000."""

    def test_list_views_default_top(self):
        m = ListViewsInput(dataverse_url=_BASE_URL)
        assert m.top == 50

    def test_list_views_rejects_zero(self):
        with pytest.raises(ValidationError):
            ListViewsInput(dataverse_url=_BASE_URL, top=0)

    def test_list_views_rejects_over_limit(self):
        with pytest.raises(ValidationError):
            ListViewsInput(dataverse_url=_BASE_URL, top=5001)

    def test_list_views_accepts_boundary_values(self):
        assert ListViewsInput(dataverse_url=_BASE_URL, top=1).top == 1
        assert ListViewsInput(dataverse_url=_BASE_URL, top=5000).top == 5000

    def test_list_forms_default_top(self):
        m = ListFormsInput(dataverse_url=_BASE_URL)
        assert m.top == 50

    def test_list_forms_rejects_zero(self):
        with pytest.raises(ValidationError):
            ListFormsInput(dataverse_url=_BASE_URL, top=0)

    def test_list_forms_rejects_over_limit(self):
        with pytest.raises(ValidationError):
            ListFormsInput(dataverse_url=_BASE_URL, top=5001)

    def test_list_apps_default_top(self):
        m = ListAppsInput(dataverse_url=_BASE_URL)
        assert m.top == 50

    def test_list_apps_rejects_zero(self):
        with pytest.raises(ValidationError):
            ListAppsInput(dataverse_url=_BASE_URL, top=0)

    def test_list_apps_rejects_over_limit(self):
        with pytest.raises(ValidationError):
            ListAppsInput(dataverse_url=_BASE_URL, top=5001)

    def test_list_tables_default_top(self):
        m = ListTablesInput(dataverse_url=_BASE_URL)
        assert m.top == 50

    def test_list_tables_rejects_zero(self):
        with pytest.raises(ValidationError):
            ListTablesInput(dataverse_url=_BASE_URL, top=0)

    def test_list_tables_rejects_over_limit(self):
        with pytest.raises(ValidationError):
            ListTablesInput(dataverse_url=_BASE_URL, top=5001)


# ---------------------------------------------------------------------------
# dataverse_list_views — has_more and count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListViewsPaging:
    """dataverse_list_views must honour top, return count and has_more."""

    async def _call(self, records: list[dict], top: int = 3) -> dict:
        app_ctx = _make_app_ctx()
        ctx = _make_ctx(app_ctx)
        params = ListViewsInput(dataverse_url=_BASE_URL, top=top)
        with (
            patch(
                "dataverse_mcp.tools.views.build_headers",
                new=AsyncMock(return_value={"Authorization": "Bearer tok"}),
            ),
            patch(
                "dataverse_mcp.tools.views.paginate_records",
                new=AsyncMock(return_value=records),
            ),
        ):
            result = await dataverse_list_views(params, ctx)
        return json.loads(result)

    async def test_has_more_false_when_below_top(self):
        data = await self._call(_fake_records(2), top=3)
        assert data["has_more"] is False
        assert data["count"] == 2

    async def test_has_more_true_when_equal_to_top(self):
        data = await self._call(_fake_records(3), top=3)
        assert data["has_more"] is True
        assert data["count"] == 3

    async def test_has_more_true_when_above_top(self):
        # paginate_records stops at top, so len == top signals more pages
        data = await self._call(_fake_records(5), top=5)
        assert data["has_more"] is True

    async def test_count_is_correct(self):
        data = await self._call(_fake_records(2), top=50)
        assert data["count"] == 2
        assert "views" in data

    async def test_empty_result(self):
        data = await self._call([], top=50)
        assert data["has_more"] is False
        assert data["count"] == 0


# ---------------------------------------------------------------------------
# dataverse_list_forms — has_more and count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListFormsPaging:
    """dataverse_list_forms must honour top, return count and has_more."""

    async def _call(self, records: list[dict], top: int = 3) -> dict:
        app_ctx = _make_app_ctx()
        ctx = _make_ctx(app_ctx)
        params = ListFormsInput(dataverse_url=_BASE_URL, top=top)
        with (
            patch(
                "dataverse_mcp.tools.forms.build_headers",
                new=AsyncMock(return_value={"Authorization": "Bearer tok"}),
            ),
            patch(
                "dataverse_mcp.tools.forms.paginate_records",
                new=AsyncMock(return_value=records),
            ),
        ):
            result = await dataverse_list_forms(params, ctx)
        return json.loads(result)

    async def test_has_more_false_when_below_top(self):
        data = await self._call(_fake_records(2), top=3)
        assert data["has_more"] is False
        assert data["count"] == 2

    async def test_has_more_true_when_equal_to_top(self):
        data = await self._call(_fake_records(3), top=3)
        assert data["has_more"] is True
        assert data["count"] == 3

    async def test_count_is_correct(self):
        data = await self._call(_fake_records(4), top=50)
        assert data["count"] == 4
        assert "forms" in data

    async def test_empty_result(self):
        data = await self._call([], top=50)
        assert data["has_more"] is False
        assert data["count"] == 0


# ---------------------------------------------------------------------------
# dataverse_list_apps — has_more and count (both URL branches)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListAppsPaging:
    """dataverse_list_apps must honour top and return has_more on both URL branches."""

    async def _call(
        self, records: list[dict], top: int = 3, include_unpublished: bool = False
    ) -> dict:
        app_ctx = _make_app_ctx()
        ctx = _make_ctx(app_ctx)
        params = ListAppsInput(
            dataverse_url=_BASE_URL,
            top=top,
            include_unpublished=include_unpublished,
        )
        with (
            patch(
                "dataverse_mcp.tools.apps.build_headers",
                new=AsyncMock(return_value={"Authorization": "Bearer tok"}),
            ),
            patch(
                "dataverse_mcp.tools.apps.paginate_records",
                new=AsyncMock(return_value=records),
            ),
        ):
            result = await dataverse_list_apps(params, ctx)
        return json.loads(result)

    async def test_has_more_false_published_branch(self):
        data = await self._call(_fake_records(2), top=3, include_unpublished=False)
        assert data["has_more"] is False
        assert data["count"] == 2

    async def test_has_more_true_published_branch(self):
        data = await self._call(_fake_records(3), top=3, include_unpublished=False)
        assert data["has_more"] is True
        assert data["count"] == 3

    async def test_has_more_false_unpublished_branch(self):
        data = await self._call(_fake_records(1), top=5, include_unpublished=True)
        assert data["has_more"] is False
        assert data["count"] == 1

    async def test_has_more_true_unpublished_branch(self):
        data = await self._call(_fake_records(5), top=5, include_unpublished=True)
        assert data["has_more"] is True
        assert data["count"] == 5

    async def test_empty_result(self):
        data = await self._call([], top=50)
        assert data["has_more"] is False
        assert data["count"] == 0

    async def test_count_matches_apps_list_length(self):
        data = await self._call(_fake_records(7), top=50)
        assert data["count"] == len(data["apps"])


# ---------------------------------------------------------------------------
# dataverse_list_tables — has_more and count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListTablesPaging:
    """dataverse_list_tables must honour top and return has_more."""

    async def _call(self, records: list[dict], top: int = 3) -> dict:
        app_ctx = _make_app_ctx()
        ctx = _make_ctx(app_ctx)
        params = ListTablesInput(dataverse_url=_BASE_URL, top=top)
        with (
            patch(
                "dataverse_mcp.tools.metadata.build_headers",
                new=AsyncMock(return_value={"Authorization": "Bearer tok"}),
            ),
            patch(
                "dataverse_mcp.tools.metadata.paginate_records",
                new=AsyncMock(return_value=records),
            ),
        ):
            result = await dataverse_list_tables(params, ctx)
        return json.loads(result)

    async def test_has_more_false_when_below_top(self):
        data = await self._call(_fake_records(2), top=3)
        assert data["has_more"] is False
        assert data["count"] == 2

    async def test_has_more_true_when_equal_to_top(self):
        data = await self._call(_fake_records(3), top=3)
        assert data["has_more"] is True
        assert data["count"] == 3

    async def test_count_is_correct(self):
        data = await self._call(_fake_records(10), top=50)
        assert data["count"] == 10
        assert "tables" in data

    async def test_empty_result(self):
        data = await self._call([], top=50)
        assert data["has_more"] is False
        assert data["count"] == 0
