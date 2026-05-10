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
# Choice (global option set) metadata tools
# ---------------------------------------------------------------------------


class ListChoicesInput(DataverseEnvironmentInput):
    """Input for listing global choice (option set) definitions in the environment."""

    select: list[str] | None = Field(
        default=None,
        description=(
            "Metadata properties to return (PascalCase). Defaults to "
            "MetadataId, Name, DisplayName, OptionSetType, IsGlobal, IsManaged. "
            "Note: 'Options' is not selectable on the list endpoint — use "
            "dataverse_get_choice to retrieve the full option values and labels "
            "for a specific choice. "
            "Example: ['Name', 'DisplayName', 'OptionSetType']"
        ),
    )
    top: int | None = Field(
        default=50,
        description="Maximum number of choices to return (1–500).",
        ge=1,
        le=500,
    )


class GetChoiceInput(DataverseEnvironmentInput):
    """Input for retrieving a single global choice by name or MetadataId."""

    name: str | None = Field(
        default=None,
        description=(
            "Logical name of the global choice "
            "(e.g., 'incident_prioritycode', 'new_my_globalchoice'). "
            "Use lowercase. Either name or metadata_id must be provided."
        ),
    )
    metadata_id: str | None = Field(
        default=None,
        description=(
            "GUID MetadataId of the global choice definition. "
            "Either name or metadata_id must be provided."
        ),
    )

    @model_validator(mode="after")
    def require_name_or_metadata_id(self) -> "GetChoiceInput":
        if not self.name and not self.metadata_id:
            raise ValueError("At least one of 'name' or 'metadata_id' must be provided.")
        return self

    @field_validator("metadata_id")
    @classmethod
    def validate_metadata_id(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError("metadata_id must be a valid GUID")
        return v


# ---------------------------------------------------------------------------
# Table schema write tools
# ---------------------------------------------------------------------------


class CreateTableInput(DataverseEnvironmentInput):
    """Input for creating a new custom table in the Dataverse environment."""

    display_name: str = Field(
        ...,
        description=(
            "Singular display name for the table (e.g., 'Widget'). "
            "Shown in the UI as the record label."
        ),
        min_length=1,
    )
    display_collection_name: str = Field(
        ...,
        description=(
            "Plural display name for the table (e.g., 'Widgets'). "
            "Used in navigation and list views."
        ),
        min_length=1,
    )
    schema_name: str = Field(
        ...,
        description=(
            "Schema name for the table. Must include a publisher prefix followed "
            "by an underscore and a PascalCase name (e.g., 'cr123_Widget', "
            "'new_MyTable'). The logical name will be derived as the lowercase "
            "version of this value."
        ),
        min_length=3,
    )
    ownership_type: str = Field(
        default="UserOwned",
        description=(
            "Ownership model for the table. "
            "'UserOwned' — records are owned by a user or team (default). "
            "'OrganizationOwned' — records are owned by the organization."
        ),
    )
    primary_name_attribute_schema_name: str = Field(
        ...,
        description=(
            "Schema name for the required primary name text column "
            "(e.g., 'cr123_Name'). Must use the same publisher prefix as schema_name."
        ),
        min_length=3,
    )
    description: str | None = Field(
        default=None,
        description="Optional description for the table.",
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the create operation. "
            "When False (default), the tool returns a preview of the entity "
            "definition that would be sent without calling the API."
        ),
    )

    @field_validator("ownership_type")
    @classmethod
    def validate_ownership_type(cls, v: str) -> str:
        allowed = ("UserOwned", "OrganizationOwned")
        if v not in allowed:
            raise ValueError(f"ownership_type must be one of: {', '.join(allowed)}")
        return v


class UpdateTableInput(DataverseEnvironmentInput):
    """Input for updating an existing table's display metadata."""

    table_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the table to update (e.g., 'account', 'cr123_widget'). "
            "Use lowercase."
        ),
        min_length=1,
    )
    display_name: str | None = Field(
        default=None,
        description="New singular display name for the table.",
    )
    description: str | None = Field(
        default=None,
        description="New description for the table.",
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the update operation. "
            "When False (default), the tool fetches the current definition, "
            "applies the requested changes, and returns a preview without calling PUT."
        ),
    )


class DeleteTableInput(DataverseEnvironmentInput):
    """Input for permanently deleting a custom table."""

    table_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the custom table to delete (e.g., 'cr123_widget'). "
            "Use lowercase. Only custom tables (IsCustomEntity=true) can be deleted."
        ),
        min_length=1,
    )
    allow_delete: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the delete operation. "
            "When False (default), the tool returns the current table definition "
            "as a preview without deleting anything. "
            "WARNING: Deletion is permanent and removes all table data."
        ),
    )


# ---------------------------------------------------------------------------
# Column schema write tools
# ---------------------------------------------------------------------------

_COLUMN_ATTRIBUTE_TYPES = (
    "String",
    "Integer",
    "Decimal",
    "DateTime",
    "Boolean",
    "Lookup",
    "Picklist",
    "MultiSelectPicklist",
)

_COLUMN_REQUIRED_LEVELS = ("None", "Recommended", "ApplicationRequired")


class CreateColumnInput(DataverseEnvironmentInput):
    """Input for adding a new column (attribute) to a Dataverse table."""

    table_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the table to add the column to "
            "(e.g., 'account', 'cr123_widget')."
        ),
        min_length=1,
    )
    schema_name: str = Field(
        ...,
        description=(
            "Schema name for the new column. Must include the publisher prefix "
            "(e.g., 'cr123_Description', 'new_Priority'). The logical name is "
            "derived as the lowercase version of this value."
        ),
        min_length=3,
    )
    attribute_type: str = Field(
        ...,
        description=(
            "Column type. One of: String, Integer, Decimal, DateTime, Boolean, "
            "Lookup, Picklist, MultiSelectPicklist."
        ),
    )
    display_name: str = Field(
        ...,
        description="Display label for the column shown in the UI.",
        min_length=1,
    )
    required_level: str | None = Field(
        default="None",
        description=(
            "Whether the column is required. One of: 'None' (optional), "
            "'Recommended', 'ApplicationRequired' (required). Defaults to 'None'."
        ),
    )
    type_specific_properties: dict | None = Field(
        default=None,
        description=(
            "Optional dict of type-specific properties merged into the attribute "
            "definition body. Examples: String → {'MaxLength': 100}; "
            "Integer → {'MinValue': 0, 'MaxValue': 100000}; "
            "Decimal → {'Precision': 2}; DateTime → {'Format': 'DateOnly'}; "
            "Boolean → {'DefaultValue': false}; "
            "Picklist → {'OptionSet': {'@odata.type': '...OptionSetMetadata', 'Options': [...]}}."
        ),
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the create operation. "
            "When False (default), the tool returns a preview of the attribute "
            "definition that would be posted without calling the API."
        ),
    )

    @field_validator("attribute_type")
    @classmethod
    def validate_attribute_type(cls, v: str) -> str:
        if v not in _COLUMN_ATTRIBUTE_TYPES:
            raise ValueError(
                f"attribute_type must be one of: {', '.join(_COLUMN_ATTRIBUTE_TYPES)}"
            )
        return v

    @field_validator("required_level")
    @classmethod
    def validate_required_level(cls, v: str | None) -> str | None:
        if v is not None and v not in _COLUMN_REQUIRED_LEVELS:
            raise ValueError(
                f"required_level must be one of: {', '.join(_COLUMN_REQUIRED_LEVELS)}"
            )
        return v


class UpdateColumnInput(DataverseEnvironmentInput):
    """Input for updating an existing column via full PUT replacement."""

    table_logical_name: str = Field(
        ...,
        description="Logical name of the table that owns the column.",
        min_length=1,
    )
    column_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the column to update (e.g., 'cr123_description'). "
            "Fetch the current definition first with dataverse_get_column."
        ),
        min_length=1,
    )
    full_definition: dict = Field(
        ...,
        description=(
            "Complete attribute definition JSON obtained from dataverse_get_column. "
            "Apply your changes to this object before passing it here. The Dataverse "
            "metadata API requires a full PUT — partial updates are not supported."
        ),
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the PUT. "
            "When False (default), returns the full_definition as a preview without "
            "calling the API."
        ),
    )


class DeleteColumnInput(DataverseEnvironmentInput):
    """Input for permanently deleting a custom column from a table."""

    table_logical_name: str = Field(
        ...,
        description="Logical name of the table that owns the column.",
        min_length=1,
    )
    column_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the column to delete (e.g., 'cr123_description'). "
            "Only custom columns can be deleted."
        ),
        min_length=1,
    )
    allow_delete: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the delete. "
            "When False (default), fetches and returns the current column definition "
            "as a preview without deleting anything. "
            "WARNING: Deletion is permanent and removes all column data."
        ),
    )


# ---------------------------------------------------------------------------
# Relationship schema write tools
# ---------------------------------------------------------------------------


class CreateOneToManyRelationshipInput(DataverseEnvironmentInput):
    """Input for creating a 1:N relationship between two tables."""

    schema_name: str = Field(
        ...,
        description=(
            "Schema name for the relationship (e.g., 'cr123_account_contacts'). "
            "Must include a publisher prefix."
        ),
        min_length=3,
    )
    referenced_entity: str = Field(
        ...,
        description=(
            "Logical name of the 'one' (referenced/parent) side table "
            "(e.g., 'account')."
        ),
        min_length=1,
    )
    referencing_entity: str = Field(
        ...,
        description=(
            "Logical name of the 'many' (referencing/child) side table "
            "(e.g., 'contact'). A lookup column is created on this table."
        ),
        min_length=1,
    )
    lookup_schema_name: str = Field(
        ...,
        description=(
            "Schema name for the lookup column created on the referencing entity "
            "(e.g., 'cr123_AccountId'). Must include a publisher prefix."
        ),
        min_length=3,
    )
    lookup_display_name: str = Field(
        ...,
        description="Display label for the lookup column.",
        min_length=1,
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the create operation. "
            "When False (default), returns a preview of the relationship "
            "definition body without calling the API."
        ),
    )


class CreateManyToManyRelationshipInput(DataverseEnvironmentInput):
    """Input for creating an N:N relationship and its intersect (junction) table."""

    schema_name: str = Field(
        ...,
        description=(
            "Schema name for the relationship (e.g., 'cr123_account_contact'). "
            "Must include a publisher prefix."
        ),
        min_length=3,
    )
    entity1_logical_name: str = Field(
        ...,
        description="Logical name of the first entity in the many-to-many relationship.",
        min_length=1,
    )
    entity2_logical_name: str = Field(
        ...,
        description="Logical name of the second entity in the many-to-many relationship.",
        min_length=1,
    )
    intersect_entity_name: str = Field(
        ...,
        description=(
            "Name for the junction (intersect) table that Dataverse creates to "
            "store the relationship links (e.g., 'cr123_account_contact')."
        ),
        min_length=1,
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the create operation. "
            "When False (default), returns a preview of the relationship "
            "definition body without calling the API."
        ),
    )


class CreateMultiTableLookupInput(DataverseEnvironmentInput):
    """Input for creating a polymorphic (multi-table) lookup column."""

    lookup_schema_name: str = Field(
        ...,
        description=(
            "Schema name for the polymorphic lookup column "
            "(e.g., 'cr123_Customer'). Must include a publisher prefix."
        ),
        min_length=3,
    )
    lookup_display_name: str = Field(
        ...,
        description="Display label for the polymorphic lookup column.",
        min_length=1,
    )
    owning_entity: str = Field(
        ...,
        description=(
            "Logical name of the table that will own the lookup column "
            "(e.g., 'cr123_order')."
        ),
        min_length=1,
    )
    target_entities: list[str] = Field(
        ...,
        description=(
            "List of table logical names the lookup can reference "
            "(e.g., ['account', 'contact']). Must contain at least one entry."
        ),
        min_length=1,
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the create operation. "
            "When False (default), returns a preview of the request body "
            "without calling the API."
        ),
    )


class UpdateRelationshipInput(DataverseEnvironmentInput):
    """Input for updating an existing relationship via full PUT replacement."""

    metadata_id: str = Field(
        ...,
        description=(
            "MetadataId GUID of the relationship to update. "
            "Obtain via dataverse_get_relationship."
        ),
        min_length=36,
    )
    full_definition: dict = Field(
        ...,
        description=(
            "Complete relationship definition JSON obtained from "
            "dataverse_get_relationship. Apply your changes before passing here. "
            "The Dataverse metadata API requires a full PUT."
        ),
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the PUT. "
            "When False (default), returns the full_definition as a preview "
            "without calling the API."
        ),
    )

    @field_validator("metadata_id")
    @classmethod
    def validate_metadata_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("metadata_id must be a valid GUID")
        return v


class DeleteRelationshipInput(DataverseEnvironmentInput):
    """Input for permanently deleting a custom relationship."""

    metadata_id: str = Field(
        ...,
        description=(
            "MetadataId GUID of the relationship to delete. "
            "Obtain via dataverse_get_relationship."
        ),
        min_length=36,
    )
    allow_delete: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the delete. "
            "When False (default), returns the metadata_id as a preview "
            "without deleting anything. "
            "WARNING: Deletion is permanent and removes the relationship and "
            "the associated lookup column."
        ),
    )

    @field_validator("metadata_id")
    @classmethod
    def validate_metadata_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("metadata_id must be a valid GUID")
        return v


# ---------------------------------------------------------------------------
# Choice (option set) write tools
# ---------------------------------------------------------------------------


class ChoiceOptionItem(BaseModel):
    """A single option value and label for a choice column."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    value: int = Field(
        ...,
        description="Integer option code (e.g., 100000000).",
    )
    label: str = Field(
        ...,
        description="Display label for the option.",
        min_length=1,
    )


class CreateChoiceInput(DataverseEnvironmentInput):
    """Input for creating a new global choice (option set)."""

    name: str = Field(
        ...,
        description=(
            "Logical name for the global choice with publisher prefix "
            "(e.g., 'cr123_mychoice'). Used to reference the choice in columns."
        ),
        min_length=3,
    )
    display_name: str = Field(
        ...,
        description="Display name for the global choice shown in the UI.",
        min_length=1,
    )
    options: list[ChoiceOptionItem] = Field(
        ...,
        description=(
            "Initial list of options. Each option requires 'value' (int) and "
            "'label' (str). Example: [{'value': 100000000, 'label': 'Option A'}]."
        ),
        min_length=1,
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the create operation. "
            "When False (default), returns a preview of the choice definition "
            "body without calling the API."
        ),
    )


class UpdateChoiceInput(DataverseEnvironmentInput):
    """Input for updating an existing global choice via full PUT replacement."""

    metadata_id: str = Field(
        ...,
        description=(
            "MetadataId GUID of the global choice to update. "
            "Obtain via dataverse_get_choice."
        ),
        min_length=36,
    )
    full_definition: dict = Field(
        ...,
        description=(
            "Complete OptionSetMetadata JSON obtained from dataverse_get_choice. "
            "Apply your changes before passing here. The Dataverse metadata API "
            "requires a full PUT. To update individual option labels, use "
            "dataverse_update_choice_option instead."
        ),
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the PUT. "
            "When False (default), returns the full_definition as a preview "
            "without calling the API."
        ),
    )

    @field_validator("metadata_id")
    @classmethod
    def validate_metadata_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("metadata_id must be a valid GUID")
        return v


class DeleteChoiceInput(DataverseEnvironmentInput):
    """Input for deleting a global choice by logical name."""

    name: str = Field(
        ...,
        description=(
            "Logical name of the global choice to delete "
            "(e.g., 'cr123_mychoice'). Confirm no columns reference it first "
            "via dataverse_get_choice."
        ),
        min_length=1,
    )
    allow_delete: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the delete. "
            "When False (default), returns the name as a preview without "
            "deleting anything."
        ),
    )


class AddChoiceOptionInput(DataverseEnvironmentInput):
    """Input for adding a new option to a local or global choice."""

    option_set_name: str | None = Field(
        default=None,
        description=(
            "Logical name of the global choice to add the option to "
            "(e.g., 'cr123_mychoice'). Provide this OR entity_logical_name + "
            "attribute_logical_name for a local choice — not both."
        ),
    )
    entity_logical_name: str | None = Field(
        default=None,
        description=(
            "Logical name of the table that owns the local choice column. "
            "Required when adding to a local (column-specific) choice."
        ),
    )
    attribute_logical_name: str | None = Field(
        default=None,
        description=(
            "Logical name of the local choice column. "
            "Required when entity_logical_name is provided."
        ),
    )
    label: str = Field(
        ...,
        description="Display label for the new option.",
        min_length=1,
    )
    value: int | None = Field(
        default=None,
        description=(
            "Integer code for the new option. If omitted, Dataverse assigns one "
            "automatically. Custom option values typically start at 100000000."
        ),
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the insert. "
            "When False (default), returns a preview of the request body "
            "without calling the API."
        ),
    )

    @model_validator(mode="after")
    def validate_choice_target(self) -> "AddChoiceOptionInput":
        has_global = bool(self.option_set_name)
        has_local = bool(self.entity_logical_name or self.attribute_logical_name)
        if has_global and has_local:
            raise ValueError(
                "Provide either option_set_name (global) or entity_logical_name + "
                "attribute_logical_name (local) — not both."
            )
        if not has_global and not has_local:
            raise ValueError(
                "Provide option_set_name (global) or entity_logical_name + "
                "attribute_logical_name (local)."
            )
        if has_local and not (self.entity_logical_name and self.attribute_logical_name):
            raise ValueError(
                "Both entity_logical_name and attribute_logical_name are required "
                "for local choices."
            )
        return self


class UpdateChoiceOptionInput(DataverseEnvironmentInput):
    """Input for updating the label of an existing option in a local or global choice."""

    option_set_name: str | None = Field(
        default=None,
        description=(
            "Logical name of the global choice (e.g., 'cr123_mychoice'). "
            "Provide this OR entity_logical_name + attribute_logical_name."
        ),
    )
    entity_logical_name: str | None = Field(
        default=None,
        description="Logical name of the table for a local choice column.",
    )
    attribute_logical_name: str | None = Field(
        default=None,
        description="Logical name of the local choice column.",
    )
    value: int = Field(
        ...,
        description="Integer code of the option to update.",
    )
    label: str = Field(
        ...,
        description="New display label for the option.",
        min_length=1,
    )
    merge_labels: bool = Field(
        default=False,
        description=(
            "When True, preserves labels for other languages and only updates "
            "the provided label. When False (default), replaces all language labels."
        ),
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the update. "
            "When False (default), returns a preview of the request body."
        ),
    )

    @model_validator(mode="after")
    def validate_choice_target(self) -> "UpdateChoiceOptionInput":
        has_global = bool(self.option_set_name)
        has_local = bool(self.entity_logical_name or self.attribute_logical_name)
        if has_global and has_local:
            raise ValueError(
                "Provide either option_set_name (global) or entity_logical_name + "
                "attribute_logical_name (local) — not both."
            )
        if not has_global and not has_local:
            raise ValueError(
                "Provide option_set_name (global) or entity_logical_name + "
                "attribute_logical_name (local)."
            )
        if has_local and not (self.entity_logical_name and self.attribute_logical_name):
            raise ValueError(
                "Both entity_logical_name and attribute_logical_name are required "
                "for local choices."
            )
        return self


class DeleteChoiceOptionInput(DataverseEnvironmentInput):
    """Input for removing a specific option value from a local or global choice."""

    option_set_name: str | None = Field(
        default=None,
        description=(
            "Logical name of the global choice (e.g., 'cr123_mychoice'). "
            "Provide this OR entity_logical_name + attribute_logical_name."
        ),
    )
    entity_logical_name: str | None = Field(
        default=None,
        description="Logical name of the table for a local choice column.",
    )
    attribute_logical_name: str | None = Field(
        default=None,
        description="Logical name of the local choice column.",
    )
    value: int = Field(
        ...,
        description="Integer code of the option to remove.",
    )
    allow_delete: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the delete. "
            "When False (default), returns a preview of the request body."
        ),
    )

    @model_validator(mode="after")
    def validate_choice_target(self) -> "DeleteChoiceOptionInput":
        has_global = bool(self.option_set_name)
        has_local = bool(self.entity_logical_name or self.attribute_logical_name)
        if has_global and has_local:
            raise ValueError(
                "Provide either option_set_name (global) or entity_logical_name + "
                "attribute_logical_name (local) — not both."
            )
        if not has_global and not has_local:
            raise ValueError(
                "Provide option_set_name (global) or entity_logical_name + "
                "attribute_logical_name (local)."
            )
        if has_local and not (self.entity_logical_name and self.attribute_logical_name):
            raise ValueError(
                "Both entity_logical_name and attribute_logical_name are required "
                "for local choices."
            )
        return self


class ReorderChoiceOptionsInput(DataverseEnvironmentInput):
    """Input for reordering all options in a local or global choice."""

    option_set_name: str | None = Field(
        default=None,
        description=(
            "Logical name of the global choice (e.g., 'cr123_mychoice'). "
            "Provide this OR entity_logical_name + attribute_logical_name."
        ),
    )
    entity_logical_name: str | None = Field(
        default=None,
        description="Logical name of the table for a local choice column.",
    )
    attribute_logical_name: str | None = Field(
        default=None,
        description="Logical name of the local choice column.",
    )
    values: list[int] = Field(
        ...,
        description=(
            "Ordered list of all integer option codes in the desired display order. "
            "Must include every existing option value."
        ),
        min_length=1,
    )
    allow_write: bool = Field(
        default=False,
        description=(
            "Safety guard. Set to True to execute the reorder. "
            "When False (default), returns a preview of the request body."
        ),
    )

    @model_validator(mode="after")
    def validate_choice_target(self) -> "ReorderChoiceOptionsInput":
        has_global = bool(self.option_set_name)
        has_local = bool(self.entity_logical_name or self.attribute_logical_name)
        if has_global and has_local:
            raise ValueError(
                "Provide either option_set_name (global) or entity_logical_name + "
                "attribute_logical_name (local) — not both."
            )
        if not has_global and not has_local:
            raise ValueError(
                "Provide option_set_name (global) or entity_logical_name + "
                "attribute_logical_name (local)."
            )
        if has_local and not (self.entity_logical_name and self.attribute_logical_name):
            raise ValueError(
                "Both entity_logical_name and attribute_logical_name are required "
                "for local choices."
            )
        return self


# ---------------------------------------------------------------------------
# Service discovery tools
# ---------------------------------------------------------------------------


class WhoAmIInput(DataverseEnvironmentInput):
    """Input for the WhoAmI identity check tool."""


class GetEntitySetsInput(DataverseEnvironmentInput):
    """Input for retrieving OData EntitySet names from the service document."""

    contains: str | None = Field(
        default=None,
        description=(
            "Case-insensitive substring filter applied to EntitySet names. "
            "Use this to narrow results (e.g., 'account' returns 'accounts', "
            "'accountleads', etc.). If omitted, all entity sets are returned up "
            "to the 'top' limit."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of entity sets to return (1–1000).",
        ge=1,
        le=1000,
    )


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
