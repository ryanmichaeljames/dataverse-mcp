"""Unit coverage for the OData literal-injection fix.

Two layers guard the OData string-literal key predicates (e.g.
``EntityDefinitions(LogicalName='...')``):

1. Output encoding — ``client.encode_odata_literal`` doubles single quotes
   (OData escaping) *before* percent-encoding, so a value cannot break out of
   the literal even though Dataverse percent-decodes the URL before parsing.
2. Input validation — logical/schema/key/choice name fields enforce the
   ``_DATAVERSE_NAME_PATTERN`` identifier grammar, rejecting the payload before
   any request is built.

These are pure, network-free regression guards for both layers.
"""

from urllib.parse import quote

import pytest
from pydantic import ValidationError

from dataverse_mcp.client import encode_odata_literal
from dataverse_mcp.models import (
    DeleteColumnInput,
    GetColumnInput,
    GetTableMetadataInput,
    ListColumnsInput,
)

_URL = "https://yourorg.crm.dynamics.com"

# The value that broke out of EntityDefinitions(LogicalName='...') and navigated
# to a foreign resource before the fix.
_BREAKOUT = "account')/Attributes(LogicalName='createdon"


def test_encode_odata_literal_is_identity_for_plain_names():
    """Legitimate logical names are unchanged (no quotes to escape)."""
    assert encode_odata_literal("account") == "account"
    assert encode_odata_literal("new_customtable") == "new_customtable"


def test_encode_odata_literal_doubles_quote_before_percent_encoding():
    """A single quote becomes '' (two quotes) then %27%27 — not a lone %27.

    A lone %27 would decode back to a single quote server-side and terminate the
    literal early; the doubled form decodes to an escaped quote that stays inside.
    """
    assert encode_odata_literal("o'brien") == "o%27%27brien"


def test_encode_odata_literal_neutralizes_breakout_payload():
    """The breakout payload is confined: no lone (odd) decoded quote survives.

    Naive percent-encoding leaves each ' as a single %27 (odd count → breakout);
    the fix doubles every quote first, so the decoded form has only escaped
    quotes and cannot terminate the enclosing literal.
    """
    naive = quote(_BREAKOUT, safe="")
    fixed = encode_odata_literal(_BREAKOUT)
    assert naive.count("%27") == 2  # two lone quotes → literal breakout
    assert fixed.count("%27") == 4  # every quote doubled → stays inside literal
    assert "%2F" in fixed  # the '/' is still encoded, never a raw path separator


@pytest.mark.parametrize(
    "payload",
    [
        _BREAKOUT,
        "acc'ount",          # stray quote
        "account/../foo",    # path traversal attempt
        "account bar",       # whitespace
        "1account",          # must start with letter/underscore
        "",                  # empty
    ],
)
def test_logical_name_fields_reject_non_identifier_values(payload):
    """Injection / malformed values are rejected at the model boundary."""
    with pytest.raises(ValidationError):
        GetColumnInput(
            dataverse_url=_URL,
            table_logical_name=payload,
            column_logical_name="name",
        )


def test_logical_name_fields_accept_valid_identifiers():
    """Genuine Dataverse logical names still validate."""
    ok = GetColumnInput(
        dataverse_url=_URL,
        table_logical_name="new_CustomTable",
        column_logical_name="createdon",
    )
    assert ok.table_logical_name == "new_CustomTable"

    assert GetTableMetadataInput(dataverse_url=_URL, table_name="account").table_name == "account"
    assert ListColumnsInput(dataverse_url=_URL, table_logical_name="contact").table_logical_name == "contact"


def test_delete_column_rejects_injection():
    """The delete path (highest impact) also validates both name fields."""
    with pytest.raises(ValidationError):
        DeleteColumnInput(
            dataverse_url=_URL,
            table_logical_name="account')/Attributes(LogicalName='name",
            column_logical_name="name",
        )
