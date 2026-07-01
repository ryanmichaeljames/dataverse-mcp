"""Regression tests for input-validation security fixes (findings #3 and #4).

Finding #3: entity_set_name fields lacked format validation, enabling path-traversal
into other API endpoints via values like 'accounts/../<other>'. Fix: pattern
r"^[a-zA-Z_][a-zA-Z0-9_]*$" applied to every entity_set_name field in models.py.

Finding #4: BatchOperationItem.url pattern r"^/[^\\r\\n]*$" was too permissive --
it blocked CRLF injection but allowed '#' fragment delimiters and arbitrary '?'/'&'
in the path segment, enabling caller injection of extra system query options. Fix:
pattern r"^/[^\\s?#]*(\\?[^\\s#]*)?$" -- one well-formed query string is allowed,
fragments and whitespace are not.

All tests are pure unit tests; no live Dataverse connection is required.
"""

import pytest
from pydantic import ValidationError

from dataverse_mcp.models import (
    AggregateTableInput,
    AssociateRecordsInput,
    AuditUserAccessInput,
    BatchOperationItem,
    BulkUpsertInput,
    CountRecordsInput,
    CreateRecordInput,
    DeleteRecordInput,
    DisassociateRecordsInput,
    ExecuteFetchXmlInput,
    GetRecordInput,
    QueryTableInput,
    RetrievePrincipalAccessInput,
    RetrieveRecordChangeHistoryInput,
    UpdateRecordInput,
)

_BASE_URL = "https://yourorg.crm.dynamics.com"
_VALID_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

# ---------------------------------------------------------------------------
# Finding #3 — entity_set_name pattern validation
# ---------------------------------------------------------------------------

# Values that must be ACCEPTED by the pattern r"^[a-zA-Z_][a-zA-Z0-9_]*$"
_VALID_ENTITY_SET_NAMES = [
    "accounts",
    "contacts",
    "leads",
    "new_customentities",
    "cr123_mytable",
    "_underscore_start",
    "CamelCase",
    "ALLCAPS",
    "abc123",
]

# Values that must be REJECTED (path traversal, injection, invalid format)
# Note: ConfigDict(str_strip_whitespace=True) strips leading/trailing spaces before
# pattern validation, so " accounts" → "accounts" (valid). Only embedded spaces fail.
_INVALID_ENTITY_SET_NAMES = [
    "accounts/../whoami",       # path traversal — dot segments
    "accounts/contacts",        # path separator
    "accounts?$filter=1eq1",    # query string injection
    "accounts#fragment",        # fragment injection
    "accounts&injected=1",      # ampersand injection
    "123startswithdigit",       # must start with letter or underscore
    "",                         # empty
    "acc ounts",                # embedded space
    "acc-ounts",                # hyphen (not in OData collection-name grammar)
    "acc.ounts",                # dot
]


# --- QueryTableInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_query_table_valid_entity_set_name(name: str) -> None:
    """QueryTableInput must accept well-formed OData collection names."""
    params = QueryTableInput(dataverse_url=_BASE_URL, entity_set_name=name)
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_query_table_rejects_invalid_entity_set_name(name: str) -> None:
    """QueryTableInput must reject entity_set_name values that violate the pattern."""
    with pytest.raises(ValidationError):
        QueryTableInput(dataverse_url=_BASE_URL, entity_set_name=name)


# --- ExecuteFetchXmlInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_execute_fetchxml_valid_entity_set_name(name: str) -> None:
    """ExecuteFetchXmlInput must accept well-formed OData collection names."""
    params = ExecuteFetchXmlInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        fetch_xml="<fetch><entity name='account'/></fetch>",
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_execute_fetchxml_rejects_invalid_entity_set_name(name: str) -> None:
    """ExecuteFetchXmlInput must reject entity_set_name values that violate the pattern."""
    with pytest.raises(ValidationError):
        ExecuteFetchXmlInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            fetch_xml="<fetch><entity name='account'/></fetch>",
        )


# --- GetRecordInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_get_record_valid_entity_set_name(name: str) -> None:
    """GetRecordInput must accept well-formed OData collection names."""
    params = GetRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        record_id=_VALID_GUID,
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_get_record_rejects_invalid_entity_set_name(name: str) -> None:
    """GetRecordInput must reject entity_set_name values that violate the pattern."""
    with pytest.raises(ValidationError):
        GetRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            record_id=_VALID_GUID,
        )


# --- AggregateTableInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_aggregate_table_valid_entity_set_name(name: str) -> None:
    """AggregateTableInput must accept well-formed OData collection names."""
    params = AggregateTableInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        apply="aggregate($count as total)",
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_aggregate_table_rejects_invalid_entity_set_name(name: str) -> None:
    """AggregateTableInput must reject entity_set_name values that violate the pattern."""
    with pytest.raises(ValidationError):
        AggregateTableInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            apply="aggregate($count as total)",
        )


# --- CountRecordsInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_count_records_valid_entity_set_name(name: str) -> None:
    """CountRecordsInput must accept well-formed OData collection names."""
    params = CountRecordsInput(dataverse_url=_BASE_URL, entity_set_name=name)
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_count_records_rejects_invalid_entity_set_name(name: str) -> None:
    """CountRecordsInput must reject entity_set_name values that violate the pattern."""
    with pytest.raises(ValidationError):
        CountRecordsInput(dataverse_url=_BASE_URL, entity_set_name=name)


# --- RetrievePrincipalAccessInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_retrieve_principal_access_valid_entity_set_name(name: str) -> None:
    """RetrievePrincipalAccessInput must accept well-formed OData collection names."""
    params = RetrievePrincipalAccessInput(
        dataverse_url=_BASE_URL,
        user_id=_VALID_GUID,
        entity_set_name=name,
        record_id=_VALID_GUID,
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_retrieve_principal_access_rejects_invalid_entity_set_name(name: str) -> None:
    """RetrievePrincipalAccessInput must reject entity_set_name values that violate the pattern."""
    with pytest.raises(ValidationError):
        RetrievePrincipalAccessInput(
            dataverse_url=_BASE_URL,
            user_id=_VALID_GUID,
            entity_set_name=name,
            record_id=_VALID_GUID,
        )


# --- AuditUserAccessInput.target_entity_set_name ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_audit_user_access_valid_target_entity_set_name(name: str) -> None:
    """AuditUserAccessInput must accept well-formed target_entity_set_name values."""
    params = AuditUserAccessInput(
        dataverse_url=_BASE_URL,
        user_id=_VALID_GUID,
        target_entity_set_name=name,
        target_record_id=_VALID_GUID,
    )
    assert params.target_entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_audit_user_access_rejects_invalid_target_entity_set_name(name: str) -> None:
    """AuditUserAccessInput must reject target_entity_set_name values that violate the pattern."""
    with pytest.raises(ValidationError):
        AuditUserAccessInput(
            dataverse_url=_BASE_URL,
            user_id=_VALID_GUID,
            target_entity_set_name=name,
            target_record_id=_VALID_GUID,
        )


def test_audit_user_access_target_entity_set_name_none_accepted() -> None:
    """AuditUserAccessInput must accept target_entity_set_name=None (optional field)."""
    params = AuditUserAccessInput(
        dataverse_url=_BASE_URL,
        user_id=_VALID_GUID,
        target_entity_set_name=None,
        target_record_id=None,
    )
    assert params.target_entity_set_name is None


# --- RetrieveRecordChangeHistoryInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_retrieve_record_change_history_valid_entity_set_name(name: str) -> None:
    """RetrieveRecordChangeHistoryInput must accept well-formed OData collection names."""
    params = RetrieveRecordChangeHistoryInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        record_id=_VALID_GUID,
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_retrieve_record_change_history_rejects_invalid_entity_set_name(name: str) -> None:
    """RetrieveRecordChangeHistoryInput must reject entity_set_name values violating the pattern."""
    with pytest.raises(ValidationError):
        RetrieveRecordChangeHistoryInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            record_id=_VALID_GUID,
        )


# --- AssociateRecordsInput (entity_set_name + related_entity_set_name) ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_associate_records_valid_entity_set_name(name: str) -> None:
    """AssociateRecordsInput.entity_set_name must accept well-formed collection names."""
    params = AssociateRecordsInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        record_id=_VALID_GUID,
        navigation_property="contact_customer_accounts",
        related_entity_set_name="contacts",
        related_record_id=_VALID_GUID,
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_associate_records_rejects_invalid_entity_set_name(name: str) -> None:
    """AssociateRecordsInput.entity_set_name must reject values violating the pattern."""
    with pytest.raises(ValidationError):
        AssociateRecordsInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            record_id=_VALID_GUID,
            navigation_property="contact_customer_accounts",
            related_entity_set_name="contacts",
            related_record_id=_VALID_GUID,
        )


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_associate_records_valid_related_entity_set_name(name: str) -> None:
    """AssociateRecordsInput.related_entity_set_name must accept well-formed collection names."""
    params = AssociateRecordsInput(
        dataverse_url=_BASE_URL,
        entity_set_name="accounts",
        record_id=_VALID_GUID,
        navigation_property="contact_customer_accounts",
        related_entity_set_name=name,
        related_record_id=_VALID_GUID,
    )
    assert params.related_entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_associate_records_rejects_invalid_related_entity_set_name(name: str) -> None:
    """AssociateRecordsInput.related_entity_set_name must reject values violating the pattern."""
    with pytest.raises(ValidationError):
        AssociateRecordsInput(
            dataverse_url=_BASE_URL,
            entity_set_name="accounts",
            record_id=_VALID_GUID,
            navigation_property="contact_customer_accounts",
            related_entity_set_name=name,
            related_record_id=_VALID_GUID,
        )


# --- DisassociateRecordsInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_disassociate_records_valid_entity_set_name(name: str) -> None:
    """DisassociateRecordsInput.entity_set_name must accept well-formed collection names."""
    params = DisassociateRecordsInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        record_id=_VALID_GUID,
        navigation_property="contact_customer_accounts",
        related_record_id=_VALID_GUID,
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_disassociate_records_rejects_invalid_entity_set_name(name: str) -> None:
    """DisassociateRecordsInput.entity_set_name must reject values violating the pattern."""
    with pytest.raises(ValidationError):
        DisassociateRecordsInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            record_id=_VALID_GUID,
            navigation_property="contact_customer_accounts",
            related_record_id=_VALID_GUID,
        )


# --- CreateRecordInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_create_record_valid_entity_set_name(name: str) -> None:
    """CreateRecordInput must accept well-formed OData collection names."""
    params = CreateRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        data={"name": "Test"},
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_create_record_rejects_invalid_entity_set_name(name: str) -> None:
    """CreateRecordInput must reject entity_set_name values violating the pattern."""
    with pytest.raises(ValidationError):
        CreateRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            data={"name": "Test"},
        )


# --- UpdateRecordInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_update_record_valid_entity_set_name(name: str) -> None:
    """UpdateRecordInput must accept well-formed OData collection names."""
    params = UpdateRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        record_id=_VALID_GUID,
        data={"name": "Updated"},
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_update_record_rejects_invalid_entity_set_name(name: str) -> None:
    """UpdateRecordInput must reject entity_set_name values violating the pattern."""
    with pytest.raises(ValidationError):
        UpdateRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            record_id=_VALID_GUID,
            data={"name": "Updated"},
        )


# --- DeleteRecordInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_delete_record_valid_entity_set_name(name: str) -> None:
    """DeleteRecordInput must accept well-formed OData collection names."""
    params = DeleteRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        record_id=_VALID_GUID,
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_delete_record_rejects_invalid_entity_set_name(name: str) -> None:
    """DeleteRecordInput must reject entity_set_name values violating the pattern."""
    with pytest.raises(ValidationError):
        DeleteRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            record_id=_VALID_GUID,
        )


# --- BulkUpsertInput ---


@pytest.mark.parametrize("name", _VALID_ENTITY_SET_NAMES)
def test_bulk_upsert_valid_entity_set_name(name: str) -> None:
    """BulkUpsertInput must accept well-formed OData collection names."""
    params = BulkUpsertInput(
        dataverse_url=_BASE_URL,
        entity_set_name=name,
        records=[{"name": "Test"}],
    )
    assert params.entity_set_name == name


@pytest.mark.parametrize("name", _INVALID_ENTITY_SET_NAMES)
def test_bulk_upsert_rejects_invalid_entity_set_name(name: str) -> None:
    """BulkUpsertInput must reject entity_set_name values violating the pattern."""
    with pytest.raises(ValidationError):
        BulkUpsertInput(
            dataverse_url=_BASE_URL,
            entity_set_name=name,
            records=[{"name": "Test"}],
        )


# ---------------------------------------------------------------------------
# Finding #4 — BatchOperationItem.url tightened pattern
# ---------------------------------------------------------------------------

# URLs that must be ACCEPTED by r"^/[^\s?#]*(\?[^\s#]*)?$"
# Note: query-string values with spaces must be percent-encoded in HTTP/1.1 request lines.
# Literal spaces anywhere in the URL are invalid per the HTTP spec and are rejected.
_VALID_BATCH_URLS = [
    "/accounts",
    "/contacts",
    "/accounts(aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)",
    "/contacts(00000000-0000-0000-0000-000000000001)",
    "/accounts?$select=name,accountid",
    "/accounts?$filter=statecode%20eq%200",          # percent-encoded spaces
    "/accounts?$select=name&$filter=statecode%20eq%200",
    "/new_customentities?$top=10",
    "/EntityDefinitions(LogicalName='account')",
    "/accounts?$select=name&$top=10&$skip=50",       # multiple OData query options
]

# URLs that must be REJECTED
_INVALID_BATCH_URLS = [
    # Fragment delimiters
    "/accounts#fragment",
    "/accounts(guid)#section",
    # Whitespace and control characters (rejected at HTTP/1.1 request-line level)
    "/accounts\r\nX-Injected: header",   # CRLF header injection
    "/accounts\nX-Injected: header",     # LF injection
    "/accounts\t",                       # tab character
    # Does not start with /
    "accounts",
    "https://other.crm.dynamics.com/accounts",
    # Empty
    "",
]


@pytest.mark.parametrize("url", _VALID_BATCH_URLS)
def test_batch_operation_item_valid_urls(url: str) -> None:
    """BatchOperationItem must accept legitimate relative OData URLs."""
    item = BatchOperationItem(method="GET", url=url)
    assert item.url == url


@pytest.mark.parametrize("url", [
    "/accounts#fragment",
    "/accounts(guid)#section",
    "/accounts\r\nX-Injected: header",
    "/accounts\nX-Injected: header",
    "accounts",
    "https://other.crm.dynamics.com/accounts",
    "",
])
def test_batch_operation_item_rejects_invalid_urls(url: str) -> None:
    """BatchOperationItem must reject URLs containing fragments, whitespace, or no leading slash."""
    with pytest.raises(ValidationError):
        BatchOperationItem(method="GET", url=url)


def test_batch_url_crlf_injection_rejected() -> None:
    """CRLF injection in batch URL must be rejected."""
    with pytest.raises(ValidationError):
        BatchOperationItem(method="GET", url="/accounts\r\nContent-Type: text/html")


def test_batch_url_query_string_allowed() -> None:
    """A single well-formed query string on a batch URL must be accepted."""
    item = BatchOperationItem(method="GET", url="/accounts?$select=name,accountid&$top=10")
    assert "?" in item.url


def test_batch_url_fragment_in_query_rejected() -> None:
    """A '#' anywhere in the batch URL (including query position) must be rejected."""
    with pytest.raises(ValidationError):
        BatchOperationItem(method="GET", url="/accounts?$select=name#anchor")


def test_batch_url_space_in_path_rejected() -> None:
    """Whitespace in the path segment of a batch URL must be rejected."""
    with pytest.raises(ValidationError):
        BatchOperationItem(method="GET", url="/accounts%20but%20then a space")


def test_batch_url_space_in_query_rejected() -> None:
    """A literal space in the query string of a batch URL must be rejected.

    Callers must percent-encode spaces (%20) in filter values for HTTP/1.1 compliance.
    """
    with pytest.raises(ValidationError):
        BatchOperationItem(method="GET", url="/accounts?$filter=name eq Contoso")
