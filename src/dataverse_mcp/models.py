"""Pydantic input models for all Dataverse MCP tools."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dataverse_mcp.client import normalize_dataverse_url

_GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


# ---------------------------------------------------------------------------
# Solution tools
# ---------------------------------------------------------------------------


class DataverseEnvironmentInput(BaseModel):
    """Shared environment selection input for all Dataverse tools."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    dataverse_url: str | None = Field(
        default=None,
        description=(
            "Preferred explicit Dataverse organization URL for this request "
            "(e.g., 'https://yourorg.crm.dynamics.com'). If omitted, the "
            "server falls back to DATAVERSE_URL for backward compatibility."
        ),
    )

    @field_validator("dataverse_url")
    @classmethod
    def validate_dataverse_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_dataverse_url(v)


class ListSolutionsInput(DataverseEnvironmentInput):
    """Input for listing solutions in the Dataverse environment."""

    filter: str | None = Field(
        default=None,
        description=(
            "OData $filter expression to narrow results. Use lowercase logical "
            "names (e.g., \"ismanaged eq true\", \"uniquename eq 'MyApp'\")"
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to solutionid, uniquename, "
            "friendlyname, version, ismanaged, installedon, modifiedon"
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of solutions to return",
        ge=1,
        le=5000,
    )


class GetSolutionInput(DataverseEnvironmentInput):
    """Input for retrieving a single solution by unique name or ID."""

    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "The unique name of the solution (e.g., 'MyCustomApp'). "
            "Provide either this or solution_id, not both."
        ),
    )
    solution_id: str | None = Field(
        default=None,
        description=(
            "The GUID of the solution (e.g., 'a1b2c3d4-...'). "
            "Provide either this or solution_unique_name, not both."
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description="Columns to return. Defaults to all standard solution columns.",
    )

    @field_validator("solution_id")
    @classmethod
    def validate_solution_guid(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_identifier_provided(self) -> "GetSolutionInput":
        if not self.solution_unique_name and not self.solution_id:
            raise ValueError(
                "Either solution_unique_name or solution_id must be provided"
            )
        if self.solution_unique_name and self.solution_id:
            raise ValueError(
                "Provide either solution_unique_name or solution_id, not both"
            )
        return self


class ListSolutionComponentsInput(DataverseEnvironmentInput):
    """Input for listing components within a specific solution."""

    solution_id: str = Field(
        ...,
        description="The GUID of the solution whose components to list",
        min_length=1,
    )

    @field_validator("solution_id")
    @classmethod
    def validate_solution_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    component_type: int | None = Field(
        default=None,
        description=(
            "Filter by component type code. Common values: "
            "1=Entity, 2=Attribute, 3=Relationship, 9=OptionSet, "
            "10=EntityRelationship, 26=View, 29=Workflow, 60=SystemForm, "
            "61=WebResource, 300=CanvasApp, 371=Connector"
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of components to return",
        ge=1,
        le=5000,
    )


# ---------------------------------------------------------------------------
# Table query tools
# ---------------------------------------------------------------------------


class QueryTableInput(DataverseEnvironmentInput):
    """Input for querying records from any Dataverse table."""

    table_name: str = Field(
        ...,
        description=(
            "Logical name of the table to query (e.g., 'account', 'contact', "
            "'new_customtable'). Use lowercase logical names."
        ),
        min_length=1,
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Omit to return default columns. "
            "Always specify this to reduce payload size "
            "(e.g., ['name', 'accountid', 'telephone1'])"
        ),
    )
    filter: str | None = Field(
        default=None,
        description=(
            "OData $filter expression. Use lowercase logical names. "
            "Examples: \"statecode eq 0\", "
            "\"name eq 'Contoso'\" , "
            "\"createdon gt 2024-01-01\""
        ),
    )
    orderby: list[str] | None = Field(
        default=None,
        description=(
            "Sort order. Each entry is 'column_name asc' or 'column_name desc' "
            "(e.g., ['name asc', 'createdon desc'])"
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return",
        ge=1,
        le=5000,
    )
    expand: list[str] | None = Field(
        default=None,
        description=(
            "Navigation properties to expand (case-sensitive!). "
            "Example: ['primarycontactid']"
        ),
    )


class GetRecordInput(DataverseEnvironmentInput):
    """Input for retrieving a single record by its ID."""

    table_name: str = Field(
        ...,
        description="Logical name of the table (e.g., 'account', 'contact')",
        min_length=1,
    )
    record_id: str = Field(
        ...,
        description="The GUID of the record to retrieve",
        min_length=1,
    )

    @field_validator("record_id")
    @classmethod
    def validate_record_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Omit to return default columns. "
            "Specify to reduce payload (e.g., ['name', 'telephone1'])"
        ),
    )


# ---------------------------------------------------------------------------
# Metadata tools
# ---------------------------------------------------------------------------


class ListTablesInput(DataverseEnvironmentInput):
    """Input for listing available tables/entities in the environment."""

    filter: str | None = Field(
        default=None,
        description=(
            "OData $filter for table metadata. "
            "Examples: \"IsCustomEntity eq true\", "
            "\"IsManaged eq false\""
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Metadata properties to return. "
            "Defaults to LogicalName, SchemaName, DisplayName, "
            "IsCustomEntity, IsManaged"
        ),
    )


class GetTableMetadataInput(DataverseEnvironmentInput):
    """Input for retrieving detailed metadata for a specific table."""

    table_name: str = Field(
        ...,
        description=(
            "Logical name of the table (e.g., 'account', 'contact', "
            "'new_customtable'). Use lowercase logical names."
        ),
        min_length=1,
    )


class ListColumnsInput(DataverseEnvironmentInput):
    """Input for listing column (attribute) definitions for a table."""

    table_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the table whose columns to list "
            "(e.g., 'account', 'contact', 'new_customtable'). Use lowercase."
        ),
        min_length=1,
    )
    attribute_type: str | None = Field(
        default=None,
        description=(
            "Filter columns by AttributeType. Common values: "
            "'String', 'Integer', 'Decimal', 'Double', 'Boolean', "
            "'DateTime', 'Lookup', 'Picklist', 'MultiSelectPicklist', "
            "'Memo', 'Money', 'Uniqueidentifier', 'File', 'Image'. "
            "Case-sensitive (use PascalCase)."
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Metadata properties to return (PascalCase). Defaults to "
            "LogicalName, SchemaName, AttributeType, DisplayName, "
            "RequiredLevel, IsValidForRead, IsValidForCreate, IsValidForUpdate. "
            "Example: ['LogicalName', 'AttributeType', 'MaxLength']"
        ),
    )


class GetColumnInput(DataverseEnvironmentInput):
    """Input for retrieving full metadata for a single table column."""

    table_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the table (e.g., 'account', 'contact'). "
            "Use lowercase."
        ),
        min_length=1,
    )
    column_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the column (e.g., 'name', 'telephone1', "
            "'new_customfield'). Use lowercase."
        ),
        min_length=1,
    )


class ListChoiceColumnOptionsInput(DataverseEnvironmentInput):
    """Input for listing option values for a Picklist or MultiSelectPicklist column."""

    table_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the table (e.g., 'account', 'lead'). "
            "Use lowercase."
        ),
        min_length=1,
    )
    column_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the Picklist or MultiSelectPicklist column "
            "(e.g., 'statuscode', 'new_category'). Use lowercase."
        ),
        min_length=1,
    )


# ---------------------------------------------------------------------------
# Relationship metadata tools
# ---------------------------------------------------------------------------

_RELATIONSHIP_TYPES = ("OneToMany", "ManyToOne", "ManyToMany")


class ListRelationshipsInput(DataverseEnvironmentInput):
    """Input for listing relationship definitions for a table or the whole environment."""

    table_logical_name: str | None = Field(
        default=None,
        description=(
            "Logical name of the table to scope the query to "
            "(e.g., 'account', 'contact'). "
            "If omitted, all relationships in the environment are returned."
        ),
    )
    relationship_type: str | None = Field(
        default=None,
        description=(
            "Filter by relationship type. "
            "Accepted values: 'OneToMany', 'ManyToOne', 'ManyToMany'. "
            "If omitted and table_logical_name is set, all three types are returned. "
            "Ignored when table_logical_name is omitted."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of relationships to return (1–500).",
        ge=1,
        le=500,
    )

    @field_validator("relationship_type")
    @classmethod
    def validate_relationship_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _RELATIONSHIP_TYPES:
            raise ValueError(
                f"relationship_type must be one of: {', '.join(_RELATIONSHIP_TYPES)}"
            )
        return v


class GetRelationshipInput(DataverseEnvironmentInput):
    """Input for retrieving full metadata for a single relationship by schema name."""

    schema_name: str = Field(
        ...,
        description=(
            "Schema name of the relationship (e.g., 'account_contacts', "
            "'contact_customer_accounts'). Schema names are case-sensitive and "
            "their exact casing/format depends on how the relationship was "
            "defined. Always match the SchemaName value exactly."
        ),
        min_length=1,
    )


class CheckRelationshipEligibilityInput(DataverseEnvironmentInput):
    """Input for checking whether a table can participate in a relationship."""

    table_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the table to check (e.g., 'account', 'contact'). "
            "Use lowercase."
        ),
        min_length=1,
    )
    check_type: str = Field(
        ...,
        description=(
            "The eligibility check to perform. "
            "'referenced' — can this table be the primary (one) side of a 1:N? "
            "'referencing' — can this table be the related (many) side of a 1:N? "
            "'many_to_many' — can this table participate in an N:N?"
        ),
    )

    @field_validator("check_type")
    @classmethod
    def validate_check_type(cls, v: str) -> str:
        allowed = ("referenced", "referencing", "many_to_many")
        if v not in allowed:
            raise ValueError(f"check_type must be one of: {', '.join(allowed)}")
        return v


# ---------------------------------------------------------------------------
# Power Platform admin tools
# ---------------------------------------------------------------------------


class ListEnvironmentsInput(BaseModel):
    """Input for listing Power Platform environments using the admin API."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    expand_capacity: bool = Field(
        default=False,
        description="Include capacity details for each environment",
    )
    expand_addons: bool = Field(
        default=False,
        description="Include add-on allocation details for each environment",
    )
