"""Tests proving defusedxml guards against entity-expansion DoS at all three parse sites.

These are pure unit tests — no live Dataverse connection required.

Coverage:
  (a) Billion-laughs payload fed to _validate_formxml returns a non-empty error list;
      no hang, no uncaught exception.
  (b) Comparable malicious payload raises DefusedXmlException when parsed via
      defusedxml.ElementTree.fromstring (the same path used by the views parse sites).
  (c) A normal valid minimal FormXml passes _validate_formxml with no errors.
  (d) A normal valid minimal FetchXml parses successfully via defusedxml.
"""

import defusedxml.ElementTree as DET
from defusedxml.common import DefusedXmlException

from dataverse_mcp.tools.forms import _validate_formxml

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
