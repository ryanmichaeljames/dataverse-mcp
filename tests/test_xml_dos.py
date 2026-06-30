"""Tests proving defusedxml guards against entity-expansion DoS at all three parse sites.

These are pure unit tests — no live Dataverse connection required.

Coverage:
  (a) Billion-laughs payload fed to _validate_formxml returns a non-empty error list;
      no hang, no uncaught exception.
  (b) Comparable malicious payload raises DefusedXmlException when parsed via
      defusedxml.ElementTree.fromstring (the same path used by the views parse sites).
  (c) A normal valid minimal FormXml passes _validate_formxml with no errors.
  (d) A normal valid minimal FetchXml parses successfully via defusedxml.
  (e) Billion-laughs payload in FetchXml fed to _validate_view_xml returns an error
      mentioning the forbidden-constructs rejection; no hang, no uncaught exception.
  (f) Billion-laughs payload in LayoutXml fed to _validate_view_xml returns an error
      mentioning the forbidden-constructs rejection.
  (g) Billion-laughs payload in the dataverse_validate_formxml dry-run path (caller
      formxml param) returns a valid=false JSON response; no hang.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import defusedxml.ElementTree as DET
import httpx
import pytest
from defusedxml.common import DefusedXmlException

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import ValidateFormInput
from dataverse_mcp.tools.forms import _validate_formxml, dataverse_validate_formxml
from dataverse_mcp.tools.views import _validate_view_xml

# ---------------------------------------------------------------------------
# Malicious payload — bounded entity nesting (5 levels, safe for test runner)
# ---------------------------------------------------------------------------

_BILLION_LAUGHS_XML = """\
<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;">
  <!ENTITY lol5 "&lol4;&lol4;">
]>
<root>&lol5;</root>"""

# ---------------------------------------------------------------------------
# Minimal valid FormXml (satisfies _validate_formxml structural rules)
# ---------------------------------------------------------------------------

_VALID_GUID = "{4273EDBD-AC1D-40D3-9FB2-095C621B552D}"
_VALID_FORMXML = f"""\
<form>
  <tabs>
    <tab id="{_VALID_GUID}">
      <columns>
        <column width="100%">
          <sections>
            <section id="{_VALID_GUID}">
              <rows>
                <row>
                  <cell id="{_VALID_GUID}">
                    <control classid="{_VALID_GUID}" datafieldname="name" />
                  </cell>
                </row>
              </rows>
            </section>
          </sections>
        </column>
      </columns>
    </tab>
  </tabs>
</form>"""

# ---------------------------------------------------------------------------
# Minimal valid FetchXml filter fragment
# ---------------------------------------------------------------------------

_VALID_FETCHXML = """\
<filter type="and">
  <condition attribute="statecode" operator="eq" value="0" />
</filter>"""


# ---------------------------------------------------------------------------
# (a) Malicious payload via _validate_formxml returns a non-empty error list
# ---------------------------------------------------------------------------


def test_formxml_billion_laughs_returns_error_list():
    """Entity-expansion payload must not hang and must return an error list."""
    errors = _validate_formxml(_BILLION_LAUGHS_XML)
    assert isinstance(errors, list), "expected a list return"
    assert len(errors) > 0, "expected at least one error for a malicious payload"
    assert errors[0] == "XML contains forbidden constructs (entities/DTD) and was rejected."


# ---------------------------------------------------------------------------
# (b) Malicious payload raises DefusedXmlException via the defusedxml path
# ---------------------------------------------------------------------------


def test_defusedxml_raises_on_billion_laughs():
    """defusedxml.ElementTree.fromstring must raise DefusedXmlException (not hang)."""
    raised = False
    try:
        DET.fromstring(_BILLION_LAUGHS_XML)
    except DefusedXmlException:
        raised = True
    assert raised, "expected DefusedXmlException for entity-expansion payload"


# ---------------------------------------------------------------------------
# (c) Valid FormXml passes _validate_formxml with no errors
# ---------------------------------------------------------------------------


def test_valid_formxml_passes_validation():
    """A well-formed, structurally valid FormXml must return an empty error list."""
    errors = _validate_formxml(_VALID_FORMXML)
    assert errors == [], f"expected no errors but got: {errors}"


# ---------------------------------------------------------------------------
# (d) Valid FetchXml filter parses successfully via defusedxml
# ---------------------------------------------------------------------------


def test_valid_fetchxml_parses_successfully():
    """A normal FetchXml filter fragment must parse without exception."""
    element = DET.fromstring(_VALID_FETCHXML)
    assert element is not None
    assert element.tag == "filter"


# ---------------------------------------------------------------------------
# Minimal valid FetchXml and LayoutXml for view validation tests
# ---------------------------------------------------------------------------

_VALID_FETCH_FOR_VIEW = """\
<fetch>
  <entity name="account">
    <attribute name="name" />
    <order attribute="name" />
  </entity>
</fetch>"""

_VALID_LAYOUT_FOR_VIEW = """\
<grid name="resultset" object="1" select="1">
  <row name="result">
    <cell name="name" width="300" />
  </row>
</grid>"""


# ---------------------------------------------------------------------------
# (e) Billion-laughs in FetchXml fed to _validate_view_xml is rejected
# ---------------------------------------------------------------------------


def test_view_xml_billion_laughs_in_fetchxml_returns_error():
    """Malicious FetchXml must not hang and must return a forbidden-constructs error."""
    errors = _validate_view_xml(_BILLION_LAUGHS_XML, _VALID_LAYOUT_FOR_VIEW)
    assert isinstance(errors, list), "expected a list return"
    assert len(errors) > 0, "expected at least one error for a malicious FetchXml"
    assert any("forbidden" in e.lower() or "entities" in e.lower() for e in errors), (
        f"expected a forbidden-constructs error, got: {errors}"
    )


# ---------------------------------------------------------------------------
# (f) Billion-laughs in LayoutXml fed to _validate_view_xml is rejected
# ---------------------------------------------------------------------------


def test_view_xml_billion_laughs_in_layoutxml_returns_error():
    """Malicious LayoutXml must not hang and must return a forbidden-constructs error."""
    errors = _validate_view_xml(_VALID_FETCH_FOR_VIEW, _BILLION_LAUGHS_XML)
    assert isinstance(errors, list), "expected a list return"
    assert len(errors) > 0, "expected at least one error for a malicious LayoutXml"
    assert any("forbidden" in e.lower() or "entities" in e.lower() for e in errors), (
        f"expected a forbidden-constructs error, got: {errors}"
    )


# ---------------------------------------------------------------------------
# (g) Billion-laughs in the dataverse_validate_formxml dry-run (caller formxml)
# ---------------------------------------------------------------------------


def _make_form_ctx() -> MagicMock:
    """Return a mock FastMCP Context backed by a minimal AppContext."""
    app_ctx = AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


@pytest.mark.asyncio
async def test_validate_formxml_dryrun_billion_laughs_returns_error_json():
    """Billion-laughs formxml in the dry-run path must return valid=false JSON, not hang."""
    params = ValidateFormInput(
        dataverse_url="https://yourorg.crm.dynamics.com",
        form_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        formxml=_BILLION_LAUGHS_XML,
    )
    result = json.loads(await dataverse_validate_formxml(params, _make_form_ctx()))

    assert result.get("valid") is False, f"expected valid=false, got: {result}"
    assert "errors" in result, "expected 'errors' key in response"
    errors = result["errors"]
    assert len(errors) > 0, "expected at least one error"
    # The error should mention forbidden constructs (from either _validate_formxml
    # or the DET.fromstring guard in the dry-run path)
    combined = " ".join(errors).lower()
    assert "forbidden" in combined or "entities" in combined or "dtd" in combined, (
        f"expected a forbidden-constructs message, got: {errors}"
    )
