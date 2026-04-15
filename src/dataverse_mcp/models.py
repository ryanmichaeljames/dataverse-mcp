"""Pydantic input models for all Dataverse MCP tools."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


# ---------------------------------------------------------------------------
# Solution tools
# ---------------------------------------------------------------------------


class ListSolutionsInput(BaseModel):
    """Input for listing solutions in the Dataverse environment."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

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


class GetSolutionInput(BaseModel):
    """Input for retrieving a single solution by unique name or ID."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

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


class ListSolutionComponentsInput(BaseModel):
    """Input for listing components within a specific solution."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

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


class QueryTableInput(BaseModel):
    """Input for querying records from any Dataverse table."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

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


class GetRecordInput(BaseModel):
    """Input for retrieving a single record by its ID."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

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


class ListTablesInput(BaseModel):
    """Input for listing available tables/entities in the environment."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

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


class GetTableMetadataInput(BaseModel):
    """Input for retrieving detailed metadata for a specific table."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    table_name: str = Field(
        ...,
        description=(
            "Logical name of the table (e.g., 'account', 'contact', "
            "'new_customtable'). Use lowercase logical names."
        ),
        min_length=1,
    )


# ---------------------------------------------------------------------------
# Dependency tools
# ---------------------------------------------------------------------------


class ComponentDependencyInput(BaseModel):
    """Input for component-scoped dependency functions."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    object_id: str = Field(
        ...,
        description=(
            "The GUID of the solution component to inspect "
            "(e.g., 'a1b2c3d4-1234-5678-abcd-ef0123456789'). "
            "Use dataverse_list_solution_components to find component objectids."
        ),
        min_length=1,
    )
    component_type: int = Field(
        ...,
        description=(
            "The solution component type code. Common values: "
            "1=Entity, 2=Attribute, 3=Relationship, 9=OptionSet, "
            "10=EntityRelationship, 20=SecurityRole, 26=View, 29=Workflow, "
            "59=Chart, 60=SystemForm, 61=WebResource, 62=SiteMap, "
            "63=ConnectionRole, 70=FieldSecurityProfile, "
            "90=PluginType, 91=PluginAssembly, 92=SDKMessageProcessingStep, "
            "300=CanvasApp, 371=Connector, 372=EnvironmentVariableDefinition"
        ),
        ge=1,
    )

    @field_validator("object_id")
    @classmethod
    def validate_object_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class SolutionDependencyInput(BaseModel):
    """Input for solution-scoped dependency functions."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    solution_unique_name: str = Field(
        ...,
        description=(
            "The unique name of the solution (e.g., 'MyCustomApp'). "
            "Use dataverse_list_solutions to find solution unique names."
        ),
        min_length=1,
    )


class AppComponentsInput(BaseModel):
    """Input for retrieving components of a model-driven app."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    app_module_id: str = Field(
        ...,
        description=(
            "The GUID of the model-driven app (AppModule) whose components "
            "to retrieve (e.g., 'a1b2c3d4-1234-5678-abcd-ef0123456789'). "
            "Query the appmodule table to find app IDs."
        ),
        min_length=1,
    )

    @field_validator("app_module_id")
    @classmethod
    def validate_app_module_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v
