"""Pydantic input models for all Dataverse MCP tools."""

import re
from typing import Any

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

    dataverse_url: str = Field(
        ...,
        description=(
            "Required Dataverse organization URL for this request "
            "(e.g., 'https://yourorg.crm.dynamics.com')."
        ),
    )

    @field_validator("dataverse_url")
    @classmethod
    def validate_dataverse_url(cls, v: str) -> str:
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


class CreatePublisherInput(DataverseEnvironmentInput):
    """Input for creating a publisher."""

    uniquename: str = Field(
        ...,
        description=(
            "Unique name for the publisher (e.g., 'contoso'). "
            "Use lowercase logical-name style characters."
        ),
        min_length=1,
    )
    display_name: str = Field(
        ...,
        description="Friendly display name for the publisher",
        min_length=1,
    )
    customization_prefix: str = Field(
        ...,
        description=(
            "Customization prefix for publisher-owned components "
            "(e.g., 'new')."
        ),
        min_length=1,
    )
    option_value_prefix: int = Field(
        ...,
        description="Customization option value prefix (e.g., 10000)",
        ge=1,
        le=2147483647,
    )


class UpdatePublisherInput(DataverseEnvironmentInput):
    """Input for updating a publisher."""

    publisher_id: str = Field(
        ...,
        description="GUID of the publisher to update",
        min_length=1,
    )
    display_name: str | None = Field(
        default=None,
        description="Updated friendly display name",
    )
    customization_prefix: str | None = Field(
        default=None,
        description="Updated customization prefix",
    )
    option_value_prefix: int | None = Field(
        default=None,
        description="Updated customization option value prefix",
        ge=1,
        le=2147483647,
    )

    @field_validator("publisher_id")
    @classmethod
    def validate_publisher_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_has_updates(self) -> "UpdatePublisherInput":
        if (
            self.display_name is None
            and self.customization_prefix is None
            and self.option_value_prefix is None
        ):
            raise ValueError(
                "At least one updatable field must be provided: "
                "display_name, customization_prefix, option_value_prefix"
            )
        return self


class _SolutionIdentifierInput(DataverseEnvironmentInput):
    """Base input requiring exactly one solution identifier."""

    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "Solution unique name. Provide either this or solution_id, not both."
        ),
    )
    solution_id: str | None = Field(
        default=None,
        description=(
            "Solution GUID. Provide either this or solution_unique_name, not both."
        ),
    )

    @field_validator("solution_id")
    @classmethod
    def validate_solution_guid(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_solution_identifier_provided(self) -> "_SolutionIdentifierInput":
        if not self.solution_unique_name and not self.solution_id:
            raise ValueError(
                "Either solution_unique_name or solution_id must be provided"
            )
        if self.solution_unique_name and self.solution_id:
            raise ValueError(
                "Provide either solution_unique_name or solution_id, not both"
            )
        return self


class GetSolutionHistoryInput(DataverseEnvironmentInput):
    """Input for retrieving a single solution history record by GUID."""

    solution_history_id: str = Field(
        ...,
        description=(
            "GUID of the msdyn_solutionhistory record to retrieve "
            "(e.g., 'a1b2c3d4-1234-5678-abcd-ef0123456789')."
        ),
        min_length=36,
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to a standard solution history projection."
        ),
    )

    @field_validator("solution_history_id")
    @classmethod
    def validate_solution_history_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListSolutionHistoriesInput(DataverseEnvironmentInput):
    """Input for listing solution history records with optional filtering."""

    solution_id: str | None = Field(
        default=None,
        description=(
            "Optional GUID of the solution to filter history records by. "
            "Provide either this or solution_unique_name, not both."
        ),
    )
    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "Optional unique name of the solution to filter history records by "
            "(e.g., 'MyCustomApp'). "
            "Provide either this or solution_id, not both."
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to a standard solution history projection."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of solution history records to return.",
        ge=1,
        le=5000,
    )

    @field_validator("solution_id")
    @classmethod
    def validate_solution_guid(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_solution_selector(self) -> "ListSolutionHistoriesInput":
        if self.solution_id and self.solution_unique_name:
            raise ValueError(
                "Provide either solution_id or solution_unique_name, not both"
            )
        return self


class CreateSolutionInput(DataverseEnvironmentInput):
    """Input for creating a solution."""

    solution_unique_name: str = Field(
        ...,
        description="Unique name for the solution (e.g., 'contoso_core')",
        min_length=1,
    )
    display_name: str = Field(
        ...,
        description="Friendly display name for the solution",
        min_length=1,
    )
    publisher_id: str = Field(
        ...,
        description="GUID of the publisher that owns this solution",
        min_length=1,
    )
    version: str = Field(
        ...,
        description="Solution version string (typically major.minor.build.revision)",
        min_length=1,
    )
    description: str | None = Field(
        default=None,
        description="Optional solution description",
    )

    @field_validator("publisher_id")
    @classmethod
    def validate_publisher_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class UpdateSolutionInput(_SolutionIdentifierInput):
    """Input for updating solution properties."""

    display_name: str | None = Field(
        default=None,
        description="Updated friendly display name",
    )
    description: str | None = Field(
        default=None,
        description="Updated solution description",
    )
    publisher_id: str | None = Field(
        default=None,
        description="Updated publisher GUID",
    )

    @field_validator("publisher_id")
    @classmethod
    def validate_publisher_guid(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_has_updates(self) -> "UpdateSolutionInput":
        if (
            self.display_name is None
            and self.description is None
            and self.publisher_id is None
        ):
            raise ValueError(
                "At least one updatable field must be provided: "
                "display_name, description, publisher_id"
            )
        return self


class UpdateSolutionVersionInput(_SolutionIdentifierInput):
    """Input for updating solution version only."""

    version: str = Field(
        ...,
        description="New solution version string",
        min_length=1,
    )


class AddComponentToSolutionInput(_SolutionIdentifierInput):
    """Input for adding a component to a solution."""

    component_id: str = Field(
        ...,
        description="GUID of the component to add",
        min_length=1,
    )
    component_type: int = Field(
        ...,
        description="Dataverse solution component type code",
    )
    add_required_components: bool = Field(
        default=False,
        description="Whether Dataverse should include required dependencies",
    )
    do_not_include_subcomponents: bool = Field(
        default=False,
        description="Whether Dataverse should skip adding subcomponents",
    )

    @field_validator("component_id")
    @classmethod
    def validate_component_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class RemoveComponentFromSolutionInput(_SolutionIdentifierInput):
    """Input for removing a component from a solution."""

    component_id: str = Field(
        ...,
        description="GUID of the underlying component to remove (RemoveSolutionComponent ComponentId)",
        min_length=1,
    )
    component_type: int = Field(
        ...,
        description="Dataverse solution component type code",
    )

    @field_validator("component_id")
    @classmethod
    def validate_component_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListCloudFlowsInput(DataverseEnvironmentInput):
    """Input for listing cloud flows."""

    filter: str | None = Field(
        default=None,
        description=(
            "Optional OData $filter expression applied to workflow rows. "
            "When omitted, defaults to cloud-flow category filtering."
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description="Columns to return. Defaults to a cloud-flow projection.",
    )
    top: int = Field(
        default=50,
        description="Maximum number of cloud flows to return",
        ge=1,
        le=5000,
    )
    solution_id: str | None = Field(
        default=None,
        description=(
            "Optional solution GUID to scope flows to a specific solution. "
            "Provide either this or solution_unique_name, not both."
        ),
    )
    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "Optional solution unique name to scope flows to a specific solution. "
            "Provide either this or solution_id, not both."
        ),
    )

    @field_validator("solution_id")
    @classmethod
    def validate_solution_guid(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_solution_selector(self) -> "ListCloudFlowsInput":
        if self.solution_id and self.solution_unique_name:
            raise ValueError(
                "Provide either solution_id or solution_unique_name, not both"
            )
        return self


class SetCloudFlowStateInput(DataverseEnvironmentInput):
    """Input for enabling or disabling a single cloud flow."""

    flow_id: str = Field(
        ...,
        description="GUID of the cloud flow workflow row",
        min_length=1,
    )
    statuscode: int | None = Field(
        default=None,
        description=(
            "Optional statuscode override. If omitted, the tool uses defaults "
            "for the requested enabled/disabled state."
        ),
    )

    @field_validator("flow_id")
    @classmethod
    def validate_flow_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class BatchSetCloudFlowsStateInput(DataverseEnvironmentInput):
    """Input for enabling or disabling cloud flows in batch."""

    flow_ids: list[str] = Field(
        ...,
        description=(
            "Ordered list of cloud flow workflow GUIDs to update. "
            "Maximum 1,000 IDs."
        ),
        min_length=1,
    )
    statuscode: int | None = Field(
        default=None,
        description=(
            "Optional statuscode override applied to all flow IDs in the batch."
        ),
    )
    continue_on_error: bool = Field(
        default=True,
        description=(
            "When True, includes Prefer: odata.continue-on-error so remaining "
            "operations continue after failures."
        ),
    )

    @field_validator("flow_ids")
    @classmethod
    def validate_flow_ids(cls, v: list[str]) -> list[str]:
        if len(v) > 1000:
            raise ValueError("flow_ids must not exceed 1,000 per request")
        seen: set[str] = set()
        for flow_id in v:
            if not _GUID_PATTERN.match(flow_id):
                raise ValueError(f"Invalid GUID format: '{flow_id}'")
            lower_id = flow_id.lower()
            if lower_id in seen:
                raise ValueError(f"Duplicate flow_id provided: '{flow_id}'")
            seen.add(lower_id)
        return v


# ---------------------------------------------------------------------------
# Solution import/export ALM tools
# ---------------------------------------------------------------------------


class ExportSolutionInput(DataverseEnvironmentInput):
    """Input for exporting a Dataverse solution as a base64-encoded zip."""

    solution_name: str = Field(
        ...,
        description=(
            "Unique name of the solution to export (e.g., 'MyCustomApp'). "
            "Use lowercase logical name — not the display name."
        ),
        min_length=1,
    )
    managed: bool = Field(
        default=False,
        description=(
            "When True, export as a managed solution. "
            "When False (default), export as unmanaged."
        ),
    )
    output_path: str | None = Field(
        default=None,
        description=(
            "Local filesystem path to write the exported solution .zip to "
            "(e.g., '/tmp/MySolution.zip' or 'C:\\\\exports\\\\MySolution.zip'). "
            "When provided, the zip is decoded and written to disk; the response "
            "contains metadata only (no base64 payload). "
            "When omitted, the base64 payload is returned inline if it is under "
            "~3 MB; otherwise a structured error asks you to supply output_path."
        ),
    )
    export_general_settings: bool | None = Field(
        default=None,
        description="Export general environment settings. Omitted from request when None.",
    )
    export_customization_settings: bool | None = Field(
        default=None,
        description="Export customization settings. Omitted from request when None.",
    )
    export_email_tracking_settings: bool | None = Field(
        default=None,
        description="Export email tracking settings. Omitted from request when None.",
    )
    export_auto_numbering_settings: bool | None = Field(
        default=None,
        description="Export auto-numbering settings. Omitted from request when None.",
    )
    export_calendar_settings: bool | None = Field(
        default=None,
        description="Export calendar settings. Omitted from request when None.",
    )
    export_relationship_roles: bool | None = Field(
        default=None,
        description="Export relationship roles. Omitted from request when None.",
    )
    export_isv_config: bool | None = Field(
        default=None,
        description="Export ISV configuration. Omitted from request when None.",
    )
    export_sales: bool | None = Field(
        default=None,
        description="Export sales settings. Omitted from request when None.",
    )
    export_marketing_settings: bool | None = Field(
        default=None,
        description="Export marketing settings. Omitted from request when None.",
    )
    export_outlook_synchronization_settings: bool | None = Field(
        default=None,
        description="Export Outlook synchronization settings. Omitted from request when None.",
    )


class ImportSolutionInput(DataverseEnvironmentInput):
    """Input for importing a Dataverse solution asynchronously via ImportSolutionAsync."""

    customization_file: str | None = Field(
        default=None,
        description=(
            "Base64-encoded solution .zip content to import inline. "
            "Provide this XOR input_path — not both and not neither. "
            "Rejected if the base64 string exceeds ~3 MB; use input_path instead."
        ),
    )
    input_path: str | None = Field(
        default=None,
        description=(
            "Local filesystem path to the solution .zip to import "
            "(e.g., '/tmp/MySolution.zip'). "
            "The server reads the file and base64-encodes it before posting. "
            "Provide this XOR customization_file — not both and not neither."
        ),
    )
    overwrite_unmanaged_customizations: bool = Field(
        default=True,
        description=(
            "When True, overwrite existing unmanaged customizations with those "
            "in the solution being imported."
        ),
    )
    publish_workflows: bool = Field(
        default=True,
        description="When True, publish workflows (cloud flows) included in the solution.",
    )
    hold_for_upgrade: bool = Field(
        default=False,
        description=(
            "When True, hold the solution as a holding solution for staged upgrade. "
            "Maps to HoldingSolution in the ImportSolutionAsync request."
        ),
    )
    skip_product_update_dependencies: bool = Field(
        default=False,
        description=(
            "When True, skip enforcing product update dependencies during import."
        ),
    )
    import_job_id: str | None = Field(
        default=None,
        description=(
            "Client-supplied GUID to use as the importjob primary key. "
            "When omitted, one is generated automatically. "
            "Use this value to poll dataverse_get_import_job for progress."
        ),
    )

    @field_validator("import_job_id")
    @classmethod
    def validate_import_job_guid(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_exactly_one_source(self) -> "ImportSolutionInput":
        has_inline = bool(self.customization_file)
        has_path = bool(self.input_path)
        if has_inline and has_path:
            raise ValueError(
                "Provide customization_file (inline base64) XOR input_path (local .zip path) — not both."
            )
        if not has_inline and not has_path:
            raise ValueError(
                "Provide customization_file (inline base64) or input_path (local .zip path)."
            )
        return self


class GetImportJobInput(DataverseEnvironmentInput):
    """Input for retrieving a single importjob record by its GUID."""

    import_job_id: str = Field(
        ...,
        description=(
            "GUID of the importjob to retrieve. This is the client-supplied "
            "ImportJobId GUID returned by dataverse_import_solution."
        ),
        min_length=36,
    )
    include_data: bool = Field(
        default=False,
        description=(
            "When True, the response includes the result XML from the 'data' column. "
            "The data column can be very large — use only to inspect failure details."
        ),
    )

    @field_validator("import_job_id")
    @classmethod
    def validate_import_job_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListImportJobsInput(DataverseEnvironmentInput):
    """Input for listing importjob records."""

    solution_name: str | None = Field(
        default=None,
        description=(
            "Optional solution unique name to filter results by (e.g., 'MyCustomApp'). "
            "Maps to an OData filter on the solutionname column."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of import job records to return.",
        ge=1,
        le=5000,
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to a projection that excludes the large "
            "'data' XML column. Specify explicitly to override."
        ),
    )


class CloneSolutionAsPatchInput(_SolutionIdentifierInput):
    """Input for cloning a solution as a patch via CloneAsPatch."""

    display_name: str = Field(
        ...,
        description="Display name for the new patch solution.",
        min_length=1,
    )
    version_number: str = Field(
        ...,
        description=(
            "Version string for the patch (e.g., '1.0.0.2'). "
            "Must share the parent solution's major.minor version and be greater."
        ),
        min_length=1,
    )


# ---------------------------------------------------------------------------
# Table query tools
# ---------------------------------------------------------------------------


class QueryTableInput(DataverseEnvironmentInput):
    """Input for querying records from any Dataverse table."""

    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the table (e.g., 'accounts', 'contacts'). "
            "Use dataverse_get_entity_sets to discover the correct name."
        ),
        min_length=1,
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Omit to return a conservative default "
            "projection ('createdon','modifiedon'). "
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
    count: bool = Field(
        default=False,
        description=(
            "When True, includes total_count in the response with the number of "
            "matching records. Counts are capped at 5,000 by Dataverse."
        ),
    )
    include_formatted_values: bool = Field(
        default=False,
        description=(
            "When True, returns human-readable formatted values alongside raw values "
            "(e.g., option set labels, formatted dates). Formatted values appear as "
            "'fieldname@OData.Community.Display.V1.FormattedValue' in each record."
        ),
    )


class ExecuteFetchXmlInput(DataverseEnvironmentInput):
    """Input for executing a FetchXML query against a Dataverse table."""

    entity_set_name: str = Field(
        ...,
        description=(
            "Entity set (collection) name matching the FetchXML root entity, "
            "e.g. 'accounts'. Use dataverse_get_entity_sets to discover the correct name."
        ),
    )
    fetch_xml: str = Field(
        ...,
        description="The FetchXML query string",
    )
    include_formatted_values: bool = Field(
        default=False,
        description=(
            "When True, includes formatted (display) values for lookups, option sets, etc. "
            "Formatted values appear as "
            "'fieldname@OData.Community.Display.V1.FormattedValue' in each record."
        ),
    )

    @field_validator("fetch_xml")
    @classmethod
    def validate_fetch_xml(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped.lower().startswith("<fetch"):
            raise ValueError(
                "fetch_xml must be a valid FetchXML string starting with '<fetch ...>'"
            )
        return stripped


class GetRecordInput(DataverseEnvironmentInput):
    """Input for retrieving a single record by its ID."""

    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the table (e.g., 'accounts', 'contacts'). "
            "Use dataverse_get_entity_sets to discover the correct name."
        ),
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
            "Columns to return. Omit to return a conservative default "
            "projection ('createdon','modifiedon'). "
            "Specify to reduce payload (e.g., ['name', 'telephone1'])"
        ),
    )
    include_formatted_values: bool = Field(
        default=False,
        description=(
            "When True, returns human-readable formatted values alongside raw values "
            "(e.g., option set labels, formatted dates). Formatted values appear as "
            "'fieldname@OData.Community.Display.V1.FormattedValue' in the record."
        ),
    )


class AggregateTableInput(DataverseEnvironmentInput):
    """Input for aggregating data from a Dataverse table using $apply."""

    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the table (e.g., 'accounts', 'contacts'). "
            "Use dataverse_get_entity_sets to discover the correct name."
        ),
        min_length=1,
    )
    apply: str = Field(
        ...,
        description=(
            "OData $apply expression. Examples: "
            "\"groupby((statecode),aggregate($count as total))\" — count rows by status; "
            "\"groupby((statecode),aggregate(accountid with countdistinct as total))\" — distinct count; "
            "\"aggregate(revenue with sum as total_revenue)\" — sum a column; "
            "\"groupby((statuscode))\" — distinct values. "
            "Use 'countdistinct' not 'count'. Lookup fields cannot be used in groupby. "
            "Works on up to 50,000 records."
        ),
        min_length=1,
    )
    filter: str | None = Field(
        default=None,
        description="OData $filter expression to narrow records before aggregation.",
    )


class CountRecordsInput(DataverseEnvironmentInput):
    """Input for counting records in a Dataverse table."""

    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the table (e.g., 'accounts', 'contacts'). "
            "Use dataverse_get_entity_sets to discover the correct name."
        ),
        min_length=1,
    )
    filter: str | None = Field(
        default=None,
        description=(
            "OData $filter expression to count only matching records. "
            "Note: the count is always capped at 5,000 by Dataverse."
        ),
    )


# ---------------------------------------------------------------------------
# Metadata tools
# ---------------------------------------------------------------------------


class MetadataReadInput(DataverseEnvironmentInput):
    """Base for metadata read tools — adds cache-bypass support."""

    consistency_strong: bool = Field(
        default=False,
        description=(
            "When True, adds the 'Consistency: Strong' request header, bypassing "
            "Dataverse's 30-second metadata cache. Use immediately after creating or "
            "updating metadata to ensure the latest schema is returned. Incurs a "
            "performance penalty; omit in normal read scenarios."
        ),
    )


class MetadataWriteInput(DataverseEnvironmentInput):
    """Base for metadata write tools — adds solution association support."""

    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "Unique name of an unmanaged solution to associate this metadata change with "
            "(e.g., 'MySolution'). When provided, Dataverse automatically adds the "
            "created or updated component to that solution. Leave unset to create "
            "components outside of any solution."
        ),
    )


class ListTablesInput(MetadataReadInput):
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
    top: int = Field(
        default=50,
        description="Maximum number of records to return.",
        ge=1,
        le=5000,
    )


class GetTableMetadataInput(MetadataReadInput):
    """Input for retrieving detailed metadata for a specific table."""

    table_name: str = Field(
        ...,
        description=(
            "Logical name of the table (e.g., 'account', 'contact', "
            "'new_customtable'). Use lowercase logical names."
        ),
        min_length=1,
    )


class ListColumnsInput(MetadataReadInput):
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


class GetColumnInput(MetadataReadInput):
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


class ListChoiceColumnOptionsInput(MetadataReadInput):
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


class ListRelationshipsInput(MetadataReadInput):
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


class GetRelationshipInput(MetadataReadInput):
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


class ListChoicesInput(MetadataReadInput):
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


class GetChoiceInput(MetadataReadInput):
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


class CreateTableInput(MetadataWriteInput):
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

    @field_validator("ownership_type")
    @classmethod
    def validate_ownership_type(cls, v: str) -> str:
        allowed = ("UserOwned", "OrganizationOwned")
        if v not in allowed:
            raise ValueError(f"ownership_type must be one of: {', '.join(allowed)}")
        return v


class UpdateTableInput(MetadataWriteInput):
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


class CreateColumnInput(MetadataWriteInput):
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


class UpdateColumnInput(MetadataWriteInput):
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


# ---------------------------------------------------------------------------
# Relationship schema write tools
# ---------------------------------------------------------------------------


class CreateOneToManyRelationshipInput(MetadataWriteInput):
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


class CreateManyToManyRelationshipInput(MetadataWriteInput):
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


class CreateMultiTableLookupInput(MetadataWriteInput):
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


class UpdateRelationshipInput(MetadataWriteInput):
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


class CreateChoiceInput(MetadataWriteInput):
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


class UpdateChoiceInput(MetadataWriteInput):
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


class AddChoiceOptionInput(MetadataWriteInput):
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


class UpdateChoiceOptionInput(MetadataWriteInput):
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


class DeleteChoiceOptionInput(MetadataWriteInput):
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


class ReorderChoiceOptionsInput(MetadataWriteInput):
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
# Publish tools
# ---------------------------------------------------------------------------


class PublishCustomizationsInput(DataverseEnvironmentInput):
    """Input for publishing Dataverse customizations."""

    entities: list[str] = Field(
        default_factory=list,
        description=(
            "Logical names of tables to publish (e.g., ['account', 'contact']). "
            "Provide this to publish only specific tables and their components."
        ),
    )
    option_sets: list[str] = Field(
        default_factory=list,
        description=(
            "Logical names of global choices to publish (e.g., ['cr123_mychoice']). "
            "Provide this to publish only specific global choices."
        ),
    )
    relationships: list[str] = Field(
        default_factory=list,
        description=(
            "Schema names of relationships to publish (e.g., ['cr123_account_contacts']). "
            "Provide this to publish only specific relationships."
        ),
    )
    publish_all: bool = Field(
        default=False,
        description=(
            "When True, publishes ALL unpublished customizations in the environment "
            "using PublishAllXml. This may take several minutes for large environments. "
            "Ignores entities, option_sets, and relationships parameters when True."
        ),
    )

    @model_validator(mode="after")
    def validate_publish_target(self) -> "PublishCustomizationsInput":
        if not self.publish_all and not (
            self.entities or self.option_sets or self.relationships
        ):
            raise ValueError(
                "When publish_all is false, provide at least one target in entities, "
                "option_sets, or relationships."
            )
        return self


# ---------------------------------------------------------------------------
# Security tools
# ---------------------------------------------------------------------------


class RetrieveUserPrivilegesInput(DataverseEnvironmentInput):
    """Input for retrieving all security privileges assigned to a system user."""

    user_id: str = Field(
        ...,
        description=(
            "GUID of the system user whose privileges to retrieve. "
            "Use dataverse_whoami to get the current caller's UserId."
        ),
        min_length=36,
    )

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("user_id must be a valid GUID")
        return v


class RetrievePrincipalAccessInput(DataverseEnvironmentInput):
    """Input for checking a user's access rights to a specific record."""

    user_id: str = Field(
        ...,
        description=(
            "GUID of the system user to check access for. "
            "Use dataverse_whoami to get the current caller's UserId."
        ),
        min_length=36,
    )
    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the target record's table "
            "(e.g., 'accounts', 'contacts'). "
            "Use dataverse_get_entity_sets to discover the correct name."
        ),
        min_length=1,
    )
    record_id: str = Field(
        ...,
        description="GUID of the target record to check access against.",
        min_length=36,
    )

    @field_validator("user_id", "record_id")
    @classmethod
    def validate_guids(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("must be a valid GUID")
        return v


class ListSecurityRolesInput(DataverseEnvironmentInput):
    """Input for listing security roles in the Dataverse environment."""

    filter: str | None = Field(
        default=None,
        description=(
            "OData $filter expression to narrow results. Use lowercase logical "
            "names (e.g., \"ismanaged eq false\", "
            "\"_businessunitid_value eq '<guid>'\")"
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to roleid, name, _businessunitid_value, "
            "ismanaged, modifiedon."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of roles to return.",
        ge=1,
        le=5000,
    )


class GetSecurityRoleInput(DataverseEnvironmentInput):
    """Input for retrieving a single security role by GUID."""

    role_id: str = Field(
        ...,
        description="GUID of the security role to retrieve.",
        min_length=36,
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to roleid, name, _businessunitid_value, "
            "ismanaged, modifiedon."
        ),
    )

    @field_validator("role_id")
    @classmethod
    def validate_role_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListTeamsInput(DataverseEnvironmentInput):
    """Input for listing teams in the Dataverse environment."""

    filter: str | None = Field(
        default=None,
        description=(
            "OData $filter expression to narrow results "
            "(e.g., \"teamtype eq 0\" for owner teams, "
            "\"isdefault eq false\")."
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to teamid, name, teamtype, "
            "_businessunitid_value, isdefault, modifiedon."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of teams to return.",
        ge=1,
        le=5000,
    )


class GetTeamInput(DataverseEnvironmentInput):
    """Input for retrieving a single team by GUID."""

    team_id: str = Field(
        ...,
        description="GUID of the team to retrieve.",
        min_length=36,
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to teamid, name, teamtype, "
            "_businessunitid_value, isdefault, modifiedon."
        ),
    )

    @field_validator("team_id")
    @classmethod
    def validate_team_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListUsersInput(DataverseEnvironmentInput):
    """Input for listing system users in the Dataverse environment."""

    filter: str | None = Field(
        default=None,
        description=(
            "OData $filter expression to narrow results "
            "(e.g., \"isdisabled eq false\", "
            "\"domainname eq 'user@contoso.com'\")."
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to systemuserid, fullname, domainname, "
            "internalemailaddress, isdisabled, _businessunitid_value."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of users to return.",
        ge=1,
        le=5000,
    )


class GetUserInput(DataverseEnvironmentInput):
    """Input for retrieving a single system user by GUID."""

    user_id: str = Field(
        ...,
        description=(
            "GUID of the system user to retrieve. "
            "Use dataverse_whoami to get the current caller's UserId."
        ),
        min_length=36,
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to systemuserid, fullname, domainname, "
            "internalemailaddress, isdisabled, _businessunitid_value."
        ),
    )

    @field_validator("user_id")
    @classmethod
    def validate_user_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListBusinessUnitsInput(DataverseEnvironmentInput):
    """Input for listing business units in the Dataverse environment."""

    filter: str | None = Field(
        default=None,
        description=(
            "OData $filter expression to narrow results "
            "(e.g., \"isdisabled eq false\")."
        ),
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to businessunitid, name, "
            "_parentbusinessunitid_value, isdisabled, modifiedon."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of business units to return.",
        ge=1,
        le=5000,
    )


class AssignSecurityRoleInput(DataverseEnvironmentInput):
    """Input for assigning a security role to a user or team."""

    role_id: str = Field(
        ...,
        description="GUID of the security role to assign.",
        min_length=36,
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "GUID of the system user to assign the role to. "
            "Provide exactly one of user_id or team_id."
        ),
    )
    team_id: str | None = Field(
        default=None,
        description=(
            "GUID of the team to assign the role to. "
            "Provide exactly one of user_id or team_id."
        ),
    )

    @field_validator("role_id")
    @classmethod
    def validate_role_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @field_validator("user_id", "team_id")
    @classmethod
    def validate_optional_guid(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_exactly_one_target(self) -> "AssignSecurityRoleInput":
        has_user = self.user_id is not None
        has_team = self.team_id is not None
        if has_user and has_team:
            raise ValueError("Provide either user_id or team_id, not both.")
        if not has_user and not has_team:
            raise ValueError("Either user_id or team_id must be provided.")
        return self


class RemoveSecurityRoleInput(DataverseEnvironmentInput):
    """Input for removing a security role from a user or team."""

    role_id: str = Field(
        ...,
        description="GUID of the security role to remove.",
        min_length=36,
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "GUID of the system user to remove the role from. "
            "Provide exactly one of user_id or team_id."
        ),
    )
    team_id: str | None = Field(
        default=None,
        description=(
            "GUID of the team to remove the role from. "
            "Provide exactly one of user_id or team_id."
        ),
    )

    @field_validator("role_id")
    @classmethod
    def validate_role_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @field_validator("user_id", "team_id")
    @classmethod
    def validate_optional_guid(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_exactly_one_target(self) -> "RemoveSecurityRoleInput":
        has_user = self.user_id is not None
        has_team = self.team_id is not None
        if has_user and has_team:
            raise ValueError("Provide either user_id or team_id, not both.")
        if not has_user and not has_team:
            raise ValueError("Either user_id or team_id must be provided.")
        return self


class AddTeamMembersInput(DataverseEnvironmentInput):
    """Input for adding one or more system users to a team."""

    team_id: str = Field(
        ...,
        description="GUID of the team to add members to.",
        min_length=36,
    )
    user_ids: list[str] = Field(
        ...,
        description=(
            "List of system user GUIDs to add as team members. "
            "At least one user_id must be provided."
        ),
        min_length=1,
    )

    @field_validator("team_id")
    @classmethod
    def validate_team_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @field_validator("user_ids")
    @classmethod
    def validate_user_guids(cls, v: list[str]) -> list[str]:
        for uid in v:
            if not _GUID_PATTERN.match(uid):
                raise ValueError(f"Invalid GUID format: '{uid}'")
        return v


class RemoveTeamMembersInput(DataverseEnvironmentInput):
    """Input for removing one or more system users from a team."""

    team_id: str = Field(
        ...,
        description="GUID of the team to remove members from.",
        min_length=36,
    )
    user_ids: list[str] = Field(
        ...,
        description=(
            "List of system user GUIDs to remove from the team. "
            "At least one user_id must be provided."
        ),
        min_length=1,
    )

    @field_validator("team_id")
    @classmethod
    def validate_team_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @field_validator("user_ids")
    @classmethod
    def validate_user_guids(cls, v: list[str]) -> list[str]:
        for uid in v:
            if not _GUID_PATTERN.match(uid):
                raise ValueError(f"Invalid GUID format: '{uid}'")
        return v


class SetUserStateInput(DataverseEnvironmentInput):
    """Input for enabling or disabling a Dataverse system user."""

    user_id: str = Field(
        ...,
        description=(
            "GUID of the system user to enable or disable. "
            "Use dataverse_whoami to get the current caller's UserId."
        ),
        min_length=36,
    )
    disabled: bool = Field(
        ...,
        description=(
            "True to disable the user (statecode=1, statuscode=2); "
            "False to enable the user (statecode=0, statuscode=1)."
        ),
    )

    @field_validator("user_id")
    @classmethod
    def validate_user_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


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


# ---------------------------------------------------------------------------
# Record association write tools
# ---------------------------------------------------------------------------


class AssociateRecordsInput(DataverseEnvironmentInput):
    """Input for creating an association between two records."""

    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the primary record's table "
            "(e.g., 'accounts'). Use dataverse_get_entity_sets to discover."
        ),
        min_length=1,
    )
    record_id: str = Field(
        ...,
        description="GUID of the primary record.",
        min_length=36,
    )
    navigation_property: str = Field(
        ...,
        description=(
            "Collection-valued navigation property name on the primary entity "
            "(case-sensitive). Use dataverse_list_relationships to discover "
            "the correct name (e.g., 'contact_customer_accounts')."
        ),
        min_length=1,
    )
    related_entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the related record's table "
            "(e.g., 'contacts')."
        ),
        min_length=1,
    )
    related_record_id: str = Field(
        ...,
        description="GUID of the related record to associate.",
        min_length=36,
    )

    @field_validator("record_id", "related_record_id")
    @classmethod
    def validate_guids(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("must be a valid GUID")
        return v


class DisassociateRecordsInput(DataverseEnvironmentInput):
    """Input for removing an association between two records."""

    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the primary record's table "
            "(e.g., 'accounts')."
        ),
        min_length=1,
    )
    record_id: str = Field(
        ...,
        description="GUID of the primary record.",
        min_length=36,
    )
    navigation_property: str = Field(
        ...,
        description=(
            "Collection-valued navigation property name on the primary entity "
            "(case-sensitive). Use dataverse_list_relationships to discover."
        ),
        min_length=1,
    )
    related_record_id: str = Field(
        ...,
        description="GUID of the related record to disassociate.",
        min_length=36,
    )

    @field_validator("record_id", "related_record_id")
    @classmethod
    def validate_guids(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("must be a valid GUID")
        return v


# ---------------------------------------------------------------------------
# Record CRUD write tools
# ---------------------------------------------------------------------------


class CreateRecordInput(DataverseEnvironmentInput):
    """Input for creating a single record in a Dataverse table."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the table (e.g., 'accounts', 'contacts'). "
            "Use dataverse_get_entity_sets to discover the correct name."
        ),
        min_length=1,
    )
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Column name/value pairs for the new record. "
            "Use logical (lowercase) column names "
            "(e.g., {'name': 'Contoso', 'telephone1': '555-0100'}). "
            "Use dataverse_list_columns to discover available columns. "
            "Must contain at least one column."
        ),
        min_length=1,
    )


class UpdateRecordInput(DataverseEnvironmentInput):
    """Input for partially updating a single record in a Dataverse table."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the table (e.g., 'accounts', 'contacts'). "
            "Use dataverse_get_entity_sets to discover the correct name."
        ),
        min_length=1,
    )
    record_id: str = Field(
        ...,
        description="The GUID of the record to update.",
        min_length=1,
    )
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Partial column name/value pairs to update — only the provided columns "
            "are changed, all others are left untouched. Use logical (lowercase) "
            "column names (e.g., {'name': 'New Name', 'telephone1': '555-0200'}). "
            "Must contain at least one column."
        ),
        min_length=1,
    )

    @field_validator("record_id")
    @classmethod
    def validate_record_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class DeleteRecordInput(DataverseEnvironmentInput):
    """Input for deleting a single record from a Dataverse table."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entity_set_name: str = Field(
        ...,
        description=(
            "OData collection name of the table (e.g., 'accounts', 'contacts'). "
            "Use dataverse_get_entity_sets to discover the correct name."
        ),
        min_length=1,
    )
    record_id: str = Field(
        ...,
        description="The GUID of the record to delete.",
        min_length=1,
    )

    @field_validator("record_id")
    @classmethod
    def validate_record_guid(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


# ---------------------------------------------------------------------------
# Record merge and batch tools
# ---------------------------------------------------------------------------

_MERGE_ENTITY_TYPES = {"account", "contact", "lead", "incident"}


class MergeRecordsInput(DataverseEnvironmentInput):
    """Input for merging a subordinate record into a target record."""

    entity_logical_name: str = Field(
        ...,
        description=(
            "Logical name of the entity type to merge. "
            "Must be one of: 'account', 'contact', 'lead', 'incident'."
        ),
        min_length=1,
    )
    target_id: str = Field(
        ...,
        description="GUID of the target record to keep after the merge.",
        min_length=36,
    )
    subordinate_id: str = Field(
        ...,
        description=(
            "GUID of the subordinate record to merge into the target. "
            "The subordinate is deactivated (not deleted) after the merge."
        ),
        min_length=36,
    )
    update_content: dict | None = Field(
        default=None,
        description=(
            "Optional dict of field name/value pairs from the subordinate record "
            "to carry over to the target after the merge. "
            "Example: {'telephone1': '555-1234'}"
        ),
    )
    perform_parenting_checks: bool = Field(
        default=False,
        description=(
            "Whether to check and reparent records during the merge. "
            "Set to True only when parenting relationships must be maintained."
        ),
    )

    @field_validator("entity_logical_name")
    @classmethod
    def validate_entity_type(cls, v: str) -> str:
        if v.lower() not in _MERGE_ENTITY_TYPES:
            raise ValueError(
                f"entity_logical_name must be one of: {', '.join(sorted(_MERGE_ENTITY_TYPES))}"
            )
        return v.lower()

    @field_validator("target_id", "subordinate_id")
    @classmethod
    def validate_merge_guids(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("must be a valid GUID")
        return v


class BatchOperationItem(BaseModel):
    """A single operation within a batch request."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    method: str = Field(
        ...,
        description=(
            "HTTP method for this operation. "
            "One of: 'GET', 'POST', 'PUT', 'PATCH', 'DELETE'."
        ),
        pattern=r"^(GET|POST|PUT|PATCH|DELETE)$",
    )
    url: str = Field(
        ...,
        description=(
            "Relative URL path for this operation (e.g., '/accounts(00000000-...)'). "
            "Must start with '/'."
        ),
        min_length=1,
        pattern=r"^/[^\r\n]*$",
    )
    body: dict | None = Field(
        default=None,
        description=(
            "Optional JSON body for POST, PUT, or PATCH operations. "
            "Not used for GET or DELETE."
        ),
    )
    change_set_id: str | None = Field(
        default=None,
        description=(
            "Optional identifier to group this operation into an atomic change set. "
            "All operations sharing the same change_set_id are executed atomically — "
            "if any fails, all are rolled back. "
            "Must contain only alphanumeric characters, hyphens, and underscores."
        ),
        min_length=1,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class ExecuteBatchInput(DataverseEnvironmentInput):
    """Input for executing multiple OData operations in a single batch request."""

    operations: list[BatchOperationItem] = Field(
        ...,
        description=(
            "Ordered list of OData operations to execute in the batch. "
            "Maximum 1,000 operations per request. "
            "Operations within the same change_set_id are executed atomically."
        ),
        min_length=1,
    )
    continue_on_error: bool = Field(
        default=False,
        description=(
            "When True, adds 'Prefer: odata.continue-on-error' — the batch "
            "continues processing remaining operations even if one fails. "
            "When False (default), the batch stops on the first error."
        ),
    )

    @field_validator("operations")
    @classmethod
    def validate_operation_count(cls, v: list) -> list:
        if len(v) > 1000:
            raise ValueError("batch operations must not exceed 1,000 per request")
        return v

    @field_validator("operations")
    @classmethod
    def validate_change_set_contiguous(cls, v: list) -> list:
        """Ensure operations with the same change_set_id are contiguous."""
        seen: set[str] = set()
        current_cs: str | None = None
        for op in v:
            cs = op.change_set_id
            if cs is None:
                current_cs = None
                continue
            if cs != current_cs:
                if cs in seen:
                    raise ValueError(
                        f"Operations for change_set_id '{cs}' must be contiguous — "
                        "interleaved change sets are not allowed."
                    )
                seen.add(cs)
                current_cs = cs
        return v


# ---------------------------------------------------------------------------
# View (savedquery) tools
# ---------------------------------------------------------------------------


class ViewSort(BaseModel):
    """A single sort clause for a view's FetchXml <order>."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    attribute: str = Field(description="Logical name of the column to sort by.")
    descending: bool = Field(
        default=False,
        description="True for descending (Z→A, newest first); False for ascending.",
    )


class ListViewsInput(DataverseEnvironmentInput):
    """Input for listing saved views (savedqueries) in a Dataverse table."""

    table_logical_name: str | None = Field(
        default=None,
        description=(
            "Logical name of the table to list views for (e.g. 'account'). "
            "Omit to list views for all tables."
        ),
    )
    query_type: int | None = Field(
        default=None,
        description=(
            "Filter by querytype. 0 = main grid, 1 = advanced find, "
            "2 = associated, 4 = quick find, 64 = lookup. Omit for all."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return.",
        ge=1,
        le=5000,
    )


class GetViewInput(DataverseEnvironmentInput):
    """Input for retrieving a single view's structured layout."""

    view_id: str = Field(
        description="GUID of the view (savedquery) to retrieve.",
        min_length=36,
        max_length=36,
    )

    @field_validator("view_id")
    @classmethod
    def _v(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("view_id must be a valid GUID.")
        return v.lower()


class ValidateViewInput(DataverseEnvironmentInput):
    """Input for validating the FetchXml/LayoutXml of a view."""

    view_id: str = Field(
        description="GUID of the view to validate.",
        min_length=36,
        max_length=36,
    )

    @field_validator("view_id")
    @classmethod
    def _v(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("view_id must be a valid GUID.")
        return v.lower()


class CreateViewInput(DataverseEnvironmentInput):
    """Input for creating a new saved view."""

    table_logical_name: str = Field(
        description="Logical name of the target table.",
        min_length=1,
    )
    name: str = Field(
        description="View name shown in the view selector.",
        min_length=1,
        max_length=200,
    )
    columns: list[str] = Field(
        description="Ordered column logical names to show as grid columns.",
        min_length=1,
    )
    sort: list[ViewSort] | None = Field(
        default=None,
        description="Optional ordered sort clauses.",
    )
    filter_fetchxml: str | None = Field(
        default=None,
        description="Optional raw <filter>…</filter> FetchXml snippet to embed.",
    )
    query_type: int = Field(
        default=0,
        description="View querytype. Default 0 (main grid).",
    )
    is_default: bool = Field(
        default=False,
        description="Set this as the table's default view.",
    )
    description: str | None = Field(
        default=None,
        description="Optional description.",
    )
    widths: dict[str, int] | None = Field(
        default=None,
        description="Per-column pixel width override, e.g. {'cr123_name': 300}.",
    )
    solution_unique_name: str | None = Field(
        default=None,
        description="Add the new view to this solution.",
    )


class UpdateViewInput(DataverseEnvironmentInput):
    """Input for updating an existing saved view."""

    view_id: str = Field(
        description="GUID of the view to update.",
        min_length=36,
        max_length=36,
    )
    name: str | None = Field(
        default=None,
        description="New view name (optional).",
    )
    columns: list[str] | None = Field(
        default=None,
        description=(
            "If given, replaces the grid columns (rebuilds fetch+layout) "
            "while preserving existing filters."
        ),
    )
    sort: list[ViewSort] | None = Field(
        default=None,
        description="If given, replaces the sort order.",
    )
    filter_fetchxml: str | None = Field(
        default=None,
        description="If given, replaces the non-quickfind <filter> block.",
    )
    widths: dict[str, int] | None = Field(
        default=None,
        description="Per-column pixel width override.",
    )
    solution_unique_name: str | None = Field(
        default=None,
        description="Add the view to this solution.",
    )

    @field_validator("view_id")
    @classmethod
    def _v(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("view_id must be a valid GUID.")
        return v.lower()


class AddViewColumnInput(DataverseEnvironmentInput):
    """Input for adding a single column to a view."""

    view_id: str = Field(
        description="GUID of the view to add a column to.",
        min_length=36,
        max_length=36,
    )
    column: str = Field(
        description="Logical name of the column to add.",
        min_length=1,
    )
    width: int | None = Field(
        default=None,
        ge=1,
        description="Pixel width for the new cell (default 100).",
    )
    position: int | None = Field(
        default=None,
        ge=0,
        description="Zero-based grid position to insert at. Omit to append.",
    )
    solution_unique_name: str | None = Field(default=None)

    @field_validator("view_id")
    @classmethod
    def _v(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("view_id must be a valid GUID.")
        return v.lower()


class RemoveViewColumnInput(DataverseEnvironmentInput):
    """Input for removing a single column from a view."""

    view_id: str = Field(
        description="GUID of the view to remove a column from.",
        min_length=36,
        max_length=36,
    )
    column: str = Field(
        description="Logical name of the column to remove.",
        min_length=1,
    )
    solution_unique_name: str | None = Field(default=None)

    @field_validator("view_id")
    @classmethod
    def _v(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("view_id must be a valid GUID.")
        return v.lower()


# ---------------------------------------------------------------------------
# Form tools
# ---------------------------------------------------------------------------


class ValidateFormInput(DataverseEnvironmentInput):
    """Input for validating the FormXml of a form against structural XSD rules."""

    form_id: str = Field(
        description="GUID of the form to validate (e.g., 'd0e900e1-ddbf-434b-868a-fa48d45ea15f').",
        min_length=36,
        max_length=36,
    )
    formxml: str | None = Field(
        default=None,
        description=(
            "FormXml string to validate directly. When provided, validates this XML without "
            "fetching from Dataverse — use as a dry-run before calling dataverse_set_formxml. "
            "When omitted, fetches and validates the live form's current FormXml."
        ),
        min_length=1,
    )

    @field_validator("form_id")
    @classmethod
    def validate_form_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("form_id must be a valid GUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).")
        return v.lower()


class SetFormXmlInput(DataverseEnvironmentInput):
    """Input for replacing a form's FormXml directly."""

    form_id: str = Field(
        description="GUID of the form to update.",
        min_length=36,
        max_length=36,
    )
    formxml: str = Field(
        description=(
            "Complete replacement FormXml string. Must be well-formed XML with a <form> root. "
            "Use dataverse_get_form to retrieve the current FormXml as a starting point, and "
            "dataverse_validate_formxml with this string as a dry-run before committing."
        ),
        min_length=1,
    )
    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "If provided, adds the form to this solution after the update via "
            "AddSolutionComponent (component type 60 — System Form)."
        ),
    )

    @field_validator("form_id")
    @classmethod
    def validate_form_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("form_id must be a valid GUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).")
        return v.lower()


class ListFormsInput(DataverseEnvironmentInput):
    """Input for listing model-driven app forms."""

    table_logical_name: str | None = Field(
        default=None,
        description=(
            "Logical name of the table to filter forms by (e.g., 'account'). "
            "Omit to return forms for all tables."
        ),
    )
    form_type: int | None = Field(
        default=None,
        description=(
            "Filter by form type integer. Common values: "
            "2 = Main, 4 = Quick View, 5 = Quick Create, 9 = Card. "
            "Omit to return all types."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return.",
        ge=1,
        le=5000,
    )


class GetFormInput(DataverseEnvironmentInput):
    """Input for retrieving a single form's structured layout."""

    form_id: str = Field(
        description="GUID of the form to retrieve (e.g., 'd0e900e1-ddbf-434b-868a-fa48d45ea15f').",
        min_length=36,
        max_length=36,
    )

    @field_validator("form_id")
    @classmethod
    def validate_form_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("form_id must be a valid GUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).")
        return v.lower()


class AddFormControlInput(DataverseEnvironmentInput):
    """Input for adding a column control to a form."""

    form_id: str = Field(
        description="GUID of the form to update.",
        min_length=36,
        max_length=36,
    )
    table_logical_name: str = Field(
        description=(
            "Logical name of the table the form belongs to (e.g., 'cr123_myentity'). "
            "Used to look up column metadata to determine the correct control type."
        ),
        min_length=1,
    )
    datafieldname: str = Field(
        description="Logical name of the column to add (e.g., 'cr123_description').",
        min_length=1,
    )
    label: str | None = Field(
        default=None,
        description=(
            "Display label for the control. Defaults to the column's display name "
            "if omitted."
        ),
    )
    section_index: int = Field(
        default=0,
        description=(
            "Zero-based index of the section (within the first tab) to add the control to. "
            "Use dataverse_get_form to see the current section layout. Defaults to 0."
        ),
        ge=0,
    )
    row_index: int | None = Field(
        default=None,
        description=(
            "Zero-based position within the section to insert the control. "
            "Omit to append at the end of the section."
        ),
        ge=0,
    )
    rowspan: int | None = Field(
        default=None,
        description=(
            "Number of rows the cell spans vertically (maps to 'rowspan' on <cell> per "
            "the FormXml XSD). Omit to use the automatic default: Memo and TextArea columns "
            "default to 3 for usable height; all other types default to 1 (no rowspan set)."
        ),
        ge=1,
    )
    disabled: bool = Field(
        default=False,
        description=(
            "When True, renders the control as read-only on the form. Maps to the 'disabled' "
            "boolean attribute on <control> in the FormXml XSD."
        ),
    )
    isrequired: bool = Field(
        default=False,
        description=(
            "When True, shows the required indicator on the form control. Maps to 'isrequired' "
            "on <control> in the FormXml XSD. Distinct from the column's RequiredLevel metadata."
        ),
    )
    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "If provided, adds the form to this solution after the update via "
            "AddSolutionComponent (component type 60 — System Form)."
        ),
    )

    @field_validator("form_id")
    @classmethod
    def validate_form_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("form_id must be a valid GUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).")
        return v.lower()


class RemoveFormControlInput(DataverseEnvironmentInput):
    """Input for removing a column control from a form."""

    form_id: str = Field(
        description="GUID of the form to update.",
        min_length=36,
        max_length=36,
    )
    datafieldname: str = Field(
        description="Logical name of the column whose control should be removed.",
        min_length=1,
    )
    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "If provided, adds the form to this solution after the update via "
            "AddSolutionComponent (component type 60 — System Form)."
        ),
    )

    @field_validator("form_id")
    @classmethod
    def validate_form_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("form_id must be a valid GUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).")
        return v.lower()


# ---------------------------------------------------------------------------
# Model-driven app (AppModule) tools
# ---------------------------------------------------------------------------


class ListAppsInput(DataverseEnvironmentInput):
    """Input for listing model-driven apps."""

    include_unpublished: bool = Field(
        default=False,
        description=(
            "When true, uses RetrieveUnpublishedMultiple to include apps that have not "
            "yet been published. Defaults to false (published apps only)."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return.",
        ge=1,
        le=5000,
    )


class GetAppInput(DataverseEnvironmentInput):
    """Input for getting a single model-driven app and its components."""

    app_id: str = Field(
        description="GUID of the app (appmoduleid) to retrieve.",
        min_length=36,
        max_length=36,
    )

    @field_validator("app_id")
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("app_id must be a valid GUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).")
        return v.lower()


class CreateAppInput(DataverseEnvironmentInput):
    """Input for creating a new model-driven app."""

    name: str = Field(
        description="Display name of the app (e.g. 'My Operations App').",
        min_length=1,
        max_length=100,
    )
    unique_name: str = Field(
        description=(
            "Unique name for the app. Dataverse auto-prepends the publisher prefix "
            "(e.g. 'new_'). Use only English letters, digits, and underscores."
        ),
        min_length=1,
        max_length=100,
    )
    description: str | None = Field(
        default=None,
        description="Optional description for the app.",
    )
    tables: list[str] | None = Field(
        default=None,
        description=(
            "Logical names of tables to add as entity components and include in the "
            "auto-generated sitemap (e.g. ['account', 'contact']). Strongly recommended — "
            "apps without a sitemap fail validation and cannot be published."
        ),
    )
    run_validate: bool = Field(
        default=True,
        description=(
            "Run ValidateApp after creating. If validation errors exist, publish is skipped "
            "unless publish_anyway=true."
        ),
    )
    publish: bool = Field(
        default=True,
        description="Publish the app after creation (and successful validation).",
    )
    publish_anyway: bool = Field(
        default=False,
        description="Publish even when validation errors are present. Use with caution.",
    )


class UpdateAppInput(DataverseEnvironmentInput):
    """Input for updating a model-driven app's mutable properties."""

    app_id: str = Field(
        description="GUID of the app to update.",
        min_length=36,
        max_length=36,
    )
    name: str | None = Field(
        default=None,
        description="New display name for the app.",
        min_length=1,
        max_length=100,
    )
    description: str | None = Field(
        default=None,
        description="New description for the app.",
    )

    @field_validator("app_id")
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("app_id must be a valid GUID.")
        return v.lower()


class AppComponentSpec(BaseModel):
    """A single component to add to or remove from a model-driven app."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    type: str = Field(
        description=(
            "Component type. Valid values: 'table', 'form', 'view', 'chart', 'bpf', 'sitemap'. "
            "Use 'table' with logical_name; all others require id (GUID)."
        ),
    )
    id: str | None = Field(
        default=None,
        description="GUID of the component. Required for form, view, chart, bpf, sitemap.",
    )
    logical_name: str | None = Field(
        default=None,
        description="Table logical name. Required when type='table'.",
    )

    @model_validator(mode="after")
    def validate_component_spec(self) -> "AppComponentSpec":
        valid = {"table", "form", "view", "chart", "bpf", "sitemap"}
        t = (self.type or "").lower()
        if t not in valid:
            raise ValueError(f"type must be one of: {', '.join(sorted(valid))}")
        if t == "table":
            if not self.logical_name:
                raise ValueError("logical_name is required when type='table'.")
        else:
            if not self.id:
                raise ValueError(f"id is required when type='{t}'.")
            if not _GUID_PATTERN.match(self.id):
                raise ValueError("id must be a valid GUID.")
        return self


class AddAppComponentsInput(DataverseEnvironmentInput):
    """Input for adding components to a model-driven app."""

    app_id: str = Field(
        description="GUID of the app to add components to.",
        min_length=36,
        max_length=36,
    )
    components: list[AppComponentSpec] = Field(
        description=(
            "Components to add. Each has type and either id (GUID) or logical_name (for tables). "
            "Use dataverse_get_app to see current components before adding."
        ),
        min_length=1,
    )

    @field_validator("app_id")
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("app_id must be a valid GUID.")
        return v.lower()


class RemoveAppComponentsInput(DataverseEnvironmentInput):
    """Input for removing components from a model-driven app."""

    app_id: str = Field(
        description="GUID of the app to remove components from.",
        min_length=36,
        max_length=36,
    )
    components: list[AppComponentSpec] = Field(
        description=(
            "Components to remove. Use object_id values from dataverse_get_app. "
            "Each has type and either id (GUID) or logical_name (for tables)."
        ),
        min_length=1,
    )

    @field_validator("app_id")
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("app_id must be a valid GUID.")
        return v.lower()


class SetAppSitemapInput(DataverseEnvironmentInput):
    """Input for creating or replacing a model-driven app's sitemap."""

    app_id: str = Field(
        description="GUID of the app whose sitemap to create or update.",
        min_length=36,
        max_length=36,
    )
    tables: list[str] | None = Field(
        default=None,
        description=(
            "Flat list of table logical names — auto-generates one Area with one Group. "
            "Mutually exclusive with areas. Example: ['account', 'contact', 'opportunity']."
        ),
    )
    areas: list[dict] | None = Field(
        default=None,
        description=(
            "Structured sitemap as a list of area dicts. Each area: "
            "{title: str, id?: str, groups: [{title: str, id?: str, subareas: "
            "[{entity?: str, url?: str, title?: str, id?: str}]}]}. "
            "Mutually exclusive with tables."
        ),
    )
    area_title: str = Field(
        default="Main",
        description="Title for the auto-generated area (only used when tables is provided).",
    )
    group_title: str = Field(
        default="Workspace",
        description="Title for the auto-generated group (only used when tables is provided).",
    )

    @field_validator("app_id")
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("app_id must be a valid GUID.")
        return v.lower()

    @model_validator(mode="after")
    def validate_tables_or_areas(self) -> "SetAppSitemapInput":
        if not self.tables and not self.areas:
            raise ValueError("Supply either tables or areas.")
        if self.tables and self.areas:
            raise ValueError("Supply tables or areas, not both.")
        return self


class ValidateAppInput(DataverseEnvironmentInput):
    """Input for validating a model-driven app."""

    app_id: str = Field(
        description="GUID of the app to validate.",
        min_length=36,
        max_length=36,
    )

    @field_validator("app_id")
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("app_id must be a valid GUID.")
        return v.lower()


class PublishAppInput(DataverseEnvironmentInput):
    """Input for publishing a model-driven app."""

    app_id: str = Field(
        description="GUID of the app to publish.",
        min_length=36,
        max_length=36,
    )

    @field_validator("app_id")
    @classmethod
    def validate_app_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("app_id must be a valid GUID.")
        return v.lower()


class AssignAppRoleInput(DataverseEnvironmentInput):
    """Input for assigning or removing a security role from a model-driven app."""

    app_id: str = Field(
        description="GUID of the app.",
        min_length=36,
        max_length=36,
    )
    role_id: str = Field(
        description="GUID of the security role to associate or disassociate.",
        min_length=36,
        max_length=36,
    )
    action: str = Field(
        description="'add' to grant the role access to the app; 'remove' to revoke it.",
    )

    @field_validator("app_id", "role_id")
    @classmethod
    def validate_guids(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("Must be a valid GUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).")
        return v.lower()

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v.lower() not in {"add", "remove"}:
            raise ValueError("action must be 'add' or 'remove'.")
        return v.lower()


# ---------------------------------------------------------------------------
# Plugin performance tools
# ---------------------------------------------------------------------------


class ListPluginTypeStatisticsInput(DataverseEnvironmentInput):
    """Input for listing PluginTypeStatistic records."""

    plugin_type_id: str | None = Field(
        default=None,
        description=(
            "GUID of the plug-in type to filter by "
            "(e.g. 'a1b2c3d4-0000-0000-0000-000000000000'). "
            "Omit to return statistics for all plug-in types."
        ),
        min_length=36,
        max_length=36,
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return.",
        ge=1,
        le=5000,
    )
    include_plugin_type_details: bool = Field(
        default=False,
        description=(
            "When true, expands the plugintypeid lookup to include the plug-in "
            "type name, typename, and assemblyname."
        ),
    )

    @field_validator("plugin_type_id")
    @classmethod
    def _validate_plugin_type_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("plugin_type_id must be a valid GUID.")
        return v.lower()


# ---------------------------------------------------------------------------
# Plugin trace log tools
# ---------------------------------------------------------------------------


class GetPluginTraceLogSettingInput(DataverseEnvironmentInput):
    """Input for reading the organization plug-in trace log setting."""


class SetPluginTraceLogSettingInput(DataverseEnvironmentInput):
    """Input for updating the organization plug-in trace log setting."""

    setting: str = Field(
        description=(
            "Trace log verbosity level: 'off' (disabled), 'exception' (log only "
            "on plug-in exceptions), or 'all' (log every plug-in execution)."
        ),
    )

    @field_validator("setting")
    @classmethod
    def _validate_setting(cls, v: str) -> str:
        if v.lower() not in {"off", "exception", "all"}:
            raise ValueError("setting must be one of: 'off', 'exception', 'all'.")
        return v.lower()


class ListPluginTraceLogsInput(DataverseEnvironmentInput):
    """Input for listing plug-in trace log records."""

    type_name: str | None = Field(
        default=None,
        description=(
            "Filter by plug-in class name (typename). Supports partial match; "
            "e.g. 'MyPlugin' matches 'Acme.MyPlugin'."
        ),
        max_length=1024,
    )
    message_name: str | None = Field(
        default=None,
        description=(
            "Filter by the Dataverse message that triggered the plug-in "
            "(e.g., 'Create', 'Update', 'Delete')."
        ),
        max_length=1024,
    )
    primary_entity: str | None = Field(
        default=None,
        description=(
            "Filter by the primary entity logical name the plug-in ran against "
            "(e.g., 'account', 'contact')."
        ),
        max_length=1000,
    )
    operation_type: int | None = Field(
        default=None,
        description=(
            "Filter by operation type: 0 = Unknown, 1 = Plug-in, "
            "2 = Workflow Activity. Omit to include all types."
        ),
        ge=0,
        le=2,
    )
    exceptions_only: bool = Field(
        default=False,
        description="When true, return only records where exceptiondetails is populated.",
    )
    hours_ago: int | None = Field(
        default=None,
        description=(
            "Limit results to logs created within the last N hours. "
            "Omit to return logs regardless of age."
        ),
        ge=1,
        le=720,
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return.",
        ge=1,
        le=5000,
    )


# ---------------------------------------------------------------------------
# Connection reference tools
# ---------------------------------------------------------------------------


class ListConnectionReferencesInput(DataverseEnvironmentInput):
    """Input for listing connection references."""

    connector_id: str | None = Field(
        default=None,
        description=(
            "Filter by connector ID (e.g., '/providers/Microsoft.PowerApps/apis/shared_sharepointonline'). "
            "Exact match on the connectorid field."
        ),
        min_length=1,
    )
    statecode: int | None = Field(
        default=None,
        description="Filter by status: 0 = Active, 1 = Inactive. Omit to return all.",
        ge=0,
        le=1,
    )
    filter: str | None = Field(
        default=None,
        description=(
            "Additional OData $filter expression (e.g., \"ismanaged eq false\"). "
            "Combined with other filter parameters using 'and'."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return.",
        ge=1,
        le=5000,
    )


class GetConnectionReferenceInput(DataverseEnvironmentInput):
    """Input for retrieving a single connection reference by ID or logical name."""

    connection_reference_id: str | None = Field(
        default=None,
        description="GUID of the connection reference.",
        min_length=36,
        max_length=36,
    )
    connection_reference_logical_name: str | None = Field(
        default=None,
        description=(
            "Logical name (connectionreferencelogicalname) of the connection reference "
            "(e.g., 'new_myconnectionreference')."
        ),
        min_length=1,
    )

    @model_validator(mode="after")
    def _require_one(self) -> "GetConnectionReferenceInput":
        has_id = bool(self.connection_reference_id)
        has_name = bool(self.connection_reference_logical_name)
        if not has_id and not has_name:
            raise ValueError(
                "Provide either connection_reference_id or connection_reference_logical_name."
            )
        if has_id and has_name:
            raise ValueError(
                "Provide either connection_reference_id or connection_reference_logical_name, not both."
            )
        return self

    @field_validator("connection_reference_id")
    @classmethod
    def _validate_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("connection_reference_id must be a valid GUID.")
        return v.lower()


class CreateConnectionReferenceInput(DataverseEnvironmentInput):
    """Input for creating a connection reference."""

    display_name: str = Field(
        description="Display name for the connection reference.",
        min_length=1,
    )
    logical_name: str = Field(
        description=(
            "Unique logical name for the connection reference "
            "(e.g., 'new_myconnectionreference'). Must be unique within the environment."
        ),
        min_length=1,
    )
    connector_id: str = Field(
        description=(
            "ID of the public/shared connector this reference targets "
            "(e.g., '/providers/Microsoft.PowerApps/apis/shared_sharepointonline')."
        ),
        min_length=1,
    )
    connection_id: str | None = Field(
        default=None,
        description=(
            "ID of the connection in API hub to assign immediately. "
            "Omit to leave unassigned and wire up later via dataverse_update_connection_reference."
        ),
    )
    description: str | None = Field(
        default=None,
        description="Description of the connection reference.",
    )
    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "Unique name of the solution to associate this connection reference with. "
            "Passed as the MSCRM.SolutionUniqueName request header."
        ),
    )


class UpdateConnectionReferenceInput(DataverseEnvironmentInput):
    """Input for updating a connection reference."""

    connection_reference_id: str = Field(
        description="GUID of the connection reference to update.",
        min_length=36,
        max_length=36,
    )
    connection_id: str | None = Field(
        default=None,
        description=(
            "Connection ID in API hub to assign. Pass an empty string to clear "
            "the current connection. Omit to leave unchanged."
        ),
    )
    display_name: str | None = Field(
        default=None,
        description="New display name for the connection reference.",
        min_length=1,
    )
    description: str | None = Field(
        default=None,
        description="New description for the connection reference.",
    )
    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "Unique name of the solution to associate this connection reference with. "
            "Passed as the MSCRM.SolutionUniqueName request header on the PATCH."
        ),
    )

    @model_validator(mode="after")
    def _require_at_least_one(self) -> "UpdateConnectionReferenceInput":
        if (
            self.connection_id is None
            and self.display_name is None
            and self.description is None
            and self.solution_unique_name is None
        ):
            raise ValueError(
                "Provide at least one of: connection_id, display_name, description, solution_unique_name."
            )
        return self

    @field_validator("connection_reference_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("connection_reference_id must be a valid GUID.")
        return v.lower()


class DeleteConnectionReferenceInput(DataverseEnvironmentInput):
    """Input for deleting a connection reference."""

    connection_reference_id: str = Field(
        description="GUID of the connection reference to delete.",
        min_length=36,
        max_length=36,
    )

    @field_validator("connection_reference_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError("connection_reference_id must be a valid GUID.")
        return v.lower()


# ---------------------------------------------------------------------------
# Environment variable tools
# ---------------------------------------------------------------------------


class GetEnvironmentVariablesInput(DataverseEnvironmentInput):
    """Input for listing environment variable definitions with their current values."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(
        default=None,
        description=(
            "Schema name or display name of a single environment variable to look up. "
            "Schema name is tried first (exact match); display name is the fallback. "
            "When provided, returns that single definition merged with its current value. "
            "Cannot be combined with solution_id or solution_unique_name."
        ),
        min_length=1,
    )
    solution_id: str | None = Field(
        default=None,
        description=(
            "GUID of the solution to scope results to. "
            "Only definitions included in this solution are returned. "
            "Provide either solution_id or solution_unique_name, not both. "
            "Cannot be combined with name."
        ),
        min_length=36,
        max_length=36,
    )
    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "Unique name of the solution to scope results to "
            "(e.g., 'MyApp'). "
            "Only definitions included in this solution are returned. "
            "Provide either solution_unique_name or solution_id, not both. "
            "Cannot be combined with name."
        ),
        min_length=1,
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return.",
        ge=1,
        le=5000,
    )

    @model_validator(mode="after")
    def _validate_solution_filter(self) -> "GetEnvironmentVariablesInput":
        if self.solution_id and self.solution_unique_name:
            raise ValueError(
                "Provide either solution_id or solution_unique_name, not both."
            )
        if self.name and (self.solution_id or self.solution_unique_name):
            raise ValueError(
                "name cannot be combined with solution_id or solution_unique_name; "
                "name performs a single-record lookup."
            )
        return self

    @field_validator("solution_id")
    @classmethod
    def _validate_solution_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("solution_id must be a valid GUID.")
        return v.lower()


class CreateEnvironmentVariableInput(DataverseEnvironmentInput):
    """Input for creating a new environment variable definition and optional initial value."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schema_name: str = Field(
        description=(
            "Schema name for the environment variable definition "
            "(e.g., 'new_MyVariable'). Must include a publisher prefix and be unique."
        ),
        min_length=1,
    )
    display_name: str = Field(
        description="Display name for the environment variable.",
        min_length=1,
    )
    type: int = Field(
        description=(
            "Data type of the environment variable. "
            "100000000=String, 100000001=Number, 100000002=Boolean, "
            "100000003=JSON, 100000004=Data source, 100000005=Secret."
        ),
        ge=100000000,
        le=100000005,
    )
    default_value: str | None = Field(
        default=None,
        description=(
            "Default value stored on the definition. "
            "Used when no environment-specific value record exists."
        ),
    )
    description: str | None = Field(
        default=None,
        description="Description of the environment variable.",
    )
    value: str | None = Field(
        default=None,
        description=(
            "Initial current value to set. "
            "If provided, a bound environmentvariablevalue record is created immediately "
            "alongside the definition."
        ),
    )
    solution_unique_name: str | None = Field(
        default=None,
        description=(
            "Unique name of the solution to associate the new definition with. "
            "Passed as the MSCRM.SolutionUniqueName request header."
        ),
    )


class UpdateEnvironmentVariableInput(DataverseEnvironmentInput):
    """Input for updating an environment variable definition and/or its current value."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    environment_variable_definition_id: str = Field(
        description="GUID of the environment variable definition to update.",
        min_length=36,
        max_length=36,
    )
    display_name: str | None = Field(
        default=None,
        description="New display name for the environment variable.",
        min_length=1,
    )
    default_value: str | None = Field(
        default=None,
        description="New default value to set on the definition.",
    )
    description: str | None = Field(
        default=None,
        description="New description for the environment variable.",
    )
    value: str | None = Field(
        default=None,
        description=(
            "New current value. "
            "If an environmentvariablevalue record already exists it is PATCHed; "
            "otherwise a new one is POSTed bound to this definition."
        ),
    )

    @model_validator(mode="after")
    def _require_at_least_one(self) -> "UpdateEnvironmentVariableInput":
        if (
            self.display_name is None
            and self.default_value is None
            and self.description is None
            and self.value is None
        ):
            raise ValueError(
                "Provide at least one of: display_name, default_value, description, value."
            )
        return self

    @field_validator("environment_variable_definition_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(
                "environment_variable_definition_id must be a valid GUID."
            )
        return v.lower()


class DeleteEnvironmentVariableInput(DataverseEnvironmentInput):
    """Input for deleting an environment variable definition, its value record, or both."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    environment_variable_definition_id: str = Field(
        description=(
            "GUID of the environment variable definition. "
            "Required when target is 'definition' or 'both'. "
            "When target is 'value', this is used to locate the value record."
        ),
        min_length=36,
        max_length=36,
    )
    target: str = Field(
        description=(
            "What to delete: "
            "'definition' removes the definition record (and cascades to the value record), "
            "'value' removes only the current value record (leaving the definition intact), "
            "'both' removes the value record first then the definition."
        ),
    )

    @field_validator("target")
    @classmethod
    def _validate_target(cls, v: str) -> str:
        allowed = {"definition", "value", "both"}
        if v not in allowed:
            raise ValueError(f"target must be one of: {', '.join(sorted(allowed))}.")
        return v

    @field_validator("environment_variable_definition_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(
                "environment_variable_definition_id must be a valid GUID."
            )
        return v.lower()


# ---------------------------------------------------------------------------
# Environment variable value tools
# ---------------------------------------------------------------------------


class GetEnvironmentVariableValuesInput(DataverseEnvironmentInput):
    """Input for getting environment variable value record(s).

    Exactly one of value_id, definition_id, or name must be provided.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    value_id: str | None = Field(
        default=None,
        description=(
            "GUID of the environmentvariablevalue record to fetch directly. "
            "Mutually exclusive with definition_id and name."
        ),
        min_length=36,
        max_length=36,
    )
    definition_id: str | None = Field(
        default=None,
        description=(
            "GUID of the environment variable definition whose value record(s) "
            "to retrieve. Mutually exclusive with value_id and name."
        ),
        min_length=36,
        max_length=36,
    )
    name: str | None = Field(
        default=None,
        description=(
            "Schema name or display name of the environment variable definition. "
            "Schema name is tried first (exact match); display name is the fallback. "
            "Mutually exclusive with value_id and definition_id."
        ),
        min_length=1,
    )
    top: int = Field(
        default=50,
        description="Maximum number of value records to return.",
        ge=1,
        le=5000,
    )

    @model_validator(mode="after")
    def _validate_targeting(self) -> "GetEnvironmentVariableValuesInput":
        provided = [
            f for f in (self.value_id, self.definition_id, self.name) if f is not None
        ]
        if len(provided) != 1:
            raise ValueError(
                "Provide exactly one of: value_id, definition_id, name."
            )
        return self

    @field_validator("value_id")
    @classmethod
    def _validate_value_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("value_id must be a valid GUID.")
        return v.lower()

    @field_validator("definition_id")
    @classmethod
    def _validate_definition_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("definition_id must be a valid GUID.")
        return v.lower()


class CreateEnvironmentVariableValueInput(DataverseEnvironmentInput):
    """Input for creating an environment variable value record.

    Exactly one of definition_id or name must be provided to identify the
    parent definition. A value is always required.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    definition_id: str | None = Field(
        default=None,
        description=(
            "GUID of the environment variable definition to bind the new value to. "
            "Mutually exclusive with name."
        ),
        min_length=36,
        max_length=36,
    )
    name: str | None = Field(
        default=None,
        description=(
            "Schema name or display name of the environment variable definition. "
            "Schema name is tried first (exact match); display name is the fallback. "
            "Mutually exclusive with definition_id."
        ),
        min_length=1,
    )
    value: str = Field(
        description="The value to store in the new environmentvariablevalue record.",
        min_length=1,
    )

    @model_validator(mode="after")
    def _validate_targeting(self) -> "CreateEnvironmentVariableValueInput":
        provided = [f for f in (self.definition_id, self.name) if f is not None]
        if len(provided) != 1:
            raise ValueError(
                "Provide exactly one of: definition_id, name."
            )
        return self

    @field_validator("definition_id")
    @classmethod
    def _validate_definition_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("definition_id must be a valid GUID.")
        return v.lower()


class UpdateEnvironmentVariableValueInput(DataverseEnvironmentInput):
    """Input for updating an existing environment variable value record.

    Exactly one targeting path must be provided: value_id alone, or
    definition_id alone, or name alone. A value string is always required.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    value_id: str | None = Field(
        default=None,
        description=(
            "GUID of the environmentvariablevalue record to PATCH directly. "
            "Mutually exclusive with definition_id and name."
        ),
        min_length=36,
        max_length=36,
    )
    definition_id: str | None = Field(
        default=None,
        description=(
            "GUID of the environment variable definition. The tool looks up its "
            "value record and PATCHes it. Mutually exclusive with value_id and name."
        ),
        min_length=36,
        max_length=36,
    )
    name: str | None = Field(
        default=None,
        description=(
            "Schema name or display name of the environment variable definition. "
            "Schema name is tried first (exact match); display name is the fallback. "
            "Mutually exclusive with value_id and definition_id."
        ),
        min_length=1,
    )
    value: str = Field(
        description="The new value string to store in the value record.",
        min_length=1,
    )

    @model_validator(mode="after")
    def _validate_targeting(self) -> "UpdateEnvironmentVariableValueInput":
        provided = [
            f for f in (self.value_id, self.definition_id, self.name) if f is not None
        ]
        if len(provided) != 1:
            raise ValueError(
                "Provide exactly one of: value_id, definition_id, name."
            )
        return self

    @field_validator("value_id")
    @classmethod
    def _validate_value_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("value_id must be a valid GUID.")
        return v.lower()

    @field_validator("definition_id")
    @classmethod
    def _validate_definition_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("definition_id must be a valid GUID.")
        return v.lower()


class DeleteEnvironmentVariableValueInput(DataverseEnvironmentInput):
    """Input for deleting an environment variable value record.

    Exactly one targeting path must be provided: value_id alone, or
    definition_id alone, or name alone.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    value_id: str | None = Field(
        default=None,
        description=(
            "GUID of the environmentvariablevalue record to delete directly. "
            "Mutually exclusive with definition_id and name."
        ),
        min_length=36,
        max_length=36,
    )
    definition_id: str | None = Field(
        default=None,
        description=(
            "GUID of the environment variable definition. The tool looks up its "
            "value record and deletes it. Mutually exclusive with value_id and name."
        ),
        min_length=36,
        max_length=36,
    )
    name: str | None = Field(
        default=None,
        description=(
            "Schema name or display name of the environment variable definition. "
            "Schema name is tried first (exact match); display name is the fallback. "
            "Mutually exclusive with value_id and definition_id."
        ),
        min_length=1,
    )

    @model_validator(mode="after")
    def _validate_targeting(self) -> "DeleteEnvironmentVariableValueInput":
        provided = [
            f for f in (self.value_id, self.definition_id, self.name) if f is not None
        ]
        if len(provided) != 1:
            raise ValueError(
                "Provide exactly one of: value_id, definition_id, name."
            )
        return self

    @field_validator("value_id")
    @classmethod
    def _validate_value_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("value_id must be a valid GUID.")
        return v.lower()

    @field_validator("definition_id")
    @classmethod
    def _validate_definition_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GUID_PATTERN.match(v):
            raise ValueError("definition_id must be a valid GUID.")
        return v.lower()


# ---------------------------------------------------------------------------
# Plugin registration tools
# ---------------------------------------------------------------------------

# --- A. Plug-in assemblies ---


class GetPluginAssemblyInput(DataverseEnvironmentInput):
    """Input for retrieving a single plug-in assembly by ID."""

    assembly_id: str = Field(
        ...,
        description="GUID of the pluginassembly record to retrieve",
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to pluginassemblyid, name, version, culture, "
            "publickeytoken, isolationmode, sourcetype, description, _packageid_value. "
            "Omit 'content' (base64 DLL) unless needed — it is large."
        ),
    )

    @field_validator("assembly_id")
    @classmethod
    def validate_assembly_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListPluginAssembliesInput(DataverseEnvironmentInput):
    """Input for listing plug-in assemblies."""

    name_contains: str | None = Field(
        default=None,
        description="Substring filter on assembly name (OData contains)",
    )
    package_id: str | None = Field(
        default=None,
        description="GUID of a pluginpackage; return only assemblies in that package",
    )
    filter: str | None = Field(
        default=None,
        description="Raw OData $filter expression (escape hatch)",
    )
    select: list[str] | None = Field(
        default=None,
        description="Columns to return. Defaults to the standard assembly projection.",
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return",
        ge=1,
        le=5000,
    )

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class CreatePluginAssemblyInput(DataverseEnvironmentInput):
    """Input for uploading a new plug-in assembly from base64 DLL bytes."""

    name: str = Field(
        ...,
        min_length=1,
        description="Assembly name, typically the DLL base name e.g. 'MyOrg.Plugins'",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Base64-encoded bytes of the strong-name-signed assembly DLL",
    )
    isolation_mode: int = Field(
        default=2,
        description=(
            "1=None (full trust; not supported online), "
            "2=Sandbox (default/recommended), 3=External"
        ),
    )
    version: str | None = Field(
        default=None,
        description="Assembly version e.g. '1.0.0.0'. Derived from content if omitted.",
    )
    culture: str | None = Field(
        default=None,
        description="Culture e.g. 'neutral'",
    )
    public_key_token: str | None = Field(
        default=None,
        description="Public key token from the strong-name key",
    )
    description: str | None = Field(
        default=None,
        description="Optional description for the assembly",
    )

    @field_validator("isolation_mode")
    @classmethod
    def validate_isolation_mode(cls, v: int) -> int:
        if v not in (1, 2, 3):
            raise ValueError("isolation_mode must be 1 (None), 2 (Sandbox), or 3 (External)")
        return v


class UpdatePluginAssemblyInput(DataverseEnvironmentInput):
    """Input for updating an existing plug-in assembly record."""

    assembly_id: str = Field(
        ...,
        description="GUID of the pluginassembly record to update",
    )
    content: str | None = Field(
        default=None,
        description="New base64-encoded DLL bytes (re-deploys the assembly code)",
    )
    isolation_mode: int | None = Field(
        default=None,
        description="Updated isolation mode: 1=None, 2=Sandbox, 3=External",
    )
    version: str | None = Field(
        default=None,
        description="Updated assembly version string",
    )
    description: str | None = Field(
        default=None,
        description="Updated description",
    )

    @field_validator("assembly_id")
    @classmethod
    def validate_assembly_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @field_validator("isolation_mode")
    @classmethod
    def validate_isolation_mode(cls, v: int | None) -> int | None:
        if v is not None and v not in (1, 2, 3):
            raise ValueError("isolation_mode must be 1 (None), 2 (Sandbox), or 3 (External)")
        return v

    @model_validator(mode="after")
    def check_has_updates(self) -> "UpdatePluginAssemblyInput":
        if (
            self.content is None
            and self.isolation_mode is None
            and self.version is None
            and self.description is None
        ):
            raise ValueError(
                "At least one updatable field must be provided: "
                "content, isolation_mode, version, description"
            )
        return self


class DeletePluginAssemblyInput(DataverseEnvironmentInput):
    """Input for deleting a plug-in assembly."""

    assembly_id: str = Field(
        ...,
        description="GUID of the pluginassembly record to delete",
    )

    @field_validator("assembly_id")
    @classmethod
    def validate_assembly_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


# --- B. Plug-in packages ---


class GetPluginPackageInput(DataverseEnvironmentInput):
    """Input for retrieving a single plug-in package by ID."""

    package_id: str = Field(
        ...,
        description="GUID of the pluginpackage record to retrieve",
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to pluginpackageid, name, uniquename, version. "
            "Omit 'content' unless needed."
        ),
    )

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListPluginPackagesInput(DataverseEnvironmentInput):
    """Input for listing plug-in packages."""

    name_contains: str | None = Field(
        default=None,
        description="Substring filter on package name",
    )
    filter: str | None = Field(
        default=None,
        description="Raw OData $filter expression",
    )
    select: list[str] | None = Field(
        default=None,
        description="Columns to return. Defaults to the standard package projection.",
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return",
        ge=1,
        le=5000,
    )


class CreatePluginPackageInput(DataverseEnvironmentInput):
    """Input for uploading a new NuGet-based plug-in package."""

    name: str = Field(
        ...,
        min_length=1,
        description="Display name for the plug-in package",
    )
    unique_name: str = Field(
        ...,
        min_length=1,
        description="Publisher-prefixed unique name e.g. 'yourprefix_MyPackage'",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Base64-encoded bytes of the .nupkg file",
    )
    version: str = Field(
        ...,
        min_length=1,
        description="Package version e.g. '1.0.0'",
    )


class UpdatePluginPackageInput(DataverseEnvironmentInput):
    """Input for updating a plug-in package (re-upload content / bump version)."""

    package_id: str = Field(
        ...,
        description="GUID of the pluginpackage record to update",
    )
    content: str | None = Field(
        default=None,
        description="New base64-encoded .nupkg bytes",
    )
    version: str | None = Field(
        default=None,
        description="Updated package version string",
    )

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_has_updates(self) -> "UpdatePluginPackageInput":
        if self.content is None and self.version is None:
            raise ValueError(
                "At least one updatable field must be provided: content, version"
            )
        return self


class DeletePluginPackageInput(DataverseEnvironmentInput):
    """Input for deleting a plug-in package."""

    package_id: str = Field(
        ...,
        description="GUID of the pluginpackage record to delete",
    )

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


# --- C. Plug-in types ---


class GetPluginTypeInput(DataverseEnvironmentInput):
    """Input for retrieving a single plug-in type by ID."""

    plugin_type_id: str = Field(
        ...,
        description="GUID of the plugintype record to retrieve",
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to plugintypeid, typename, friendlyname, "
            "name, assemblyname, isworkflowactivity, _pluginassemblyid_value."
        ),
    )

    @field_validator("plugin_type_id")
    @classmethod
    def validate_plugin_type_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListPluginTypesInput(DataverseEnvironmentInput):
    """Input for listing plug-in types."""

    assembly_id: str | None = Field(
        default=None,
        description="GUID of a pluginassembly; return only types in that assembly",
    )
    typename_contains: str | None = Field(
        default=None,
        description="Substring filter on typename (fully-qualified .NET class name)",
    )
    is_workflow_activity: bool | None = Field(
        default=None,
        description="When True/False, filter to custom workflow activities only or exclude them",
    )
    filter: str | None = Field(
        default=None,
        description="Raw OData $filter expression",
    )
    select: list[str] | None = Field(
        default=None,
        description="Columns to return. Defaults to the standard type projection.",
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return",
        ge=1,
        le=5000,
    )

    @field_validator("assembly_id")
    @classmethod
    def validate_assembly_id(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class CreatePluginTypeInput(DataverseEnvironmentInput):
    """Input for registering a plug-in type (class) against an assembly."""

    assembly_id: str = Field(
        ...,
        description="GUID of the parent pluginassembly record",
    )
    typename: str = Field(
        ...,
        min_length=1,
        description="Fully-qualified .NET type name e.g. 'MyOrg.Plugins.ContactPlugin'",
    )
    friendly_name: str | None = Field(
        default=None,
        description="User-friendly name; defaults to typename if omitted",
    )
    name: str | None = Field(
        default=None,
        description="Display name; defaults to typename if omitted",
    )
    is_workflow_activity: bool = Field(
        default=False,
        description="Whether this type is a custom workflow activity",
    )
    workflow_activity_group_name: str | None = Field(
        default=None,
        description="Group name; only relevant for custom workflow activities",
    )

    @field_validator("assembly_id")
    @classmethod
    def validate_assembly_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class UpdatePluginTypeInput(DataverseEnvironmentInput):
    """Input for updating a plug-in type record."""

    plugin_type_id: str = Field(
        ...,
        description="GUID of the plugintype record to update",
    )
    friendly_name: str | None = Field(
        default=None,
        description="Updated user-friendly name",
    )
    name: str | None = Field(
        default=None,
        description="Updated display name",
    )
    workflow_activity_group_name: str | None = Field(
        default=None,
        description="Updated workflow activity group name",
    )

    @field_validator("plugin_type_id")
    @classmethod
    def validate_plugin_type_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_has_updates(self) -> "UpdatePluginTypeInput":
        if (
            self.friendly_name is None
            and self.name is None
            and self.workflow_activity_group_name is None
        ):
            raise ValueError(
                "At least one updatable field must be provided: "
                "friendly_name, name, workflow_activity_group_name"
            )
        return self


class DeletePluginTypeInput(DataverseEnvironmentInput):
    """Input for deleting a plug-in type."""

    plugin_type_id: str = Field(
        ...,
        description="GUID of the plugintype record to delete",
    )

    @field_validator("plugin_type_id")
    @classmethod
    def validate_plugin_type_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


# --- D. SDK messages ---


class GetSdkMessageInput(DataverseEnvironmentInput):
    """Input for resolving an SDK message by name or ID."""

    message_name: str | None = Field(
        default=None,
        description="Message name e.g. 'Create', 'Update', 'Delete'. Provide this OR message_id.",
    )
    message_id: str | None = Field(
        default=None,
        description="GUID of the sdkmessage record. Provide this OR message_name.",
    )

    @field_validator("message_id")
    @classmethod
    def validate_message_id(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_exactly_one_identifier(self) -> "GetSdkMessageInput":
        has_name = bool(self.message_name)
        has_id = bool(self.message_id)
        if has_name and has_id:
            raise ValueError("Provide either message_name or message_id, not both")
        if not has_name and not has_id:
            raise ValueError("Either message_name or message_id must be provided")
        return self


class ListSdkMessagesInput(DataverseEnvironmentInput):
    """Input for listing/searching SDK messages."""

    name_contains: str | None = Field(
        default=None,
        description="Substring filter on message name",
    )
    filter: str | None = Field(
        default=None,
        description="Raw OData $filter expression",
    )
    select: list[str] | None = Field(
        default=None,
        description="Columns to return. Defaults to sdkmessageid, name.",
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return",
        ge=1,
        le=5000,
    )


# --- E. SDK message filters ---


class GetSdkMessageFilterInput(DataverseEnvironmentInput):
    """Input for resolving an SDK message filter by message+entity or filter ID."""

    filter_id: str | None = Field(
        default=None,
        description=(
            "GUID of the sdkmessagefilter record. "
            "Provide this alone OR provide message_id + primary_entity."
        ),
    )
    message_id: str | None = Field(
        default=None,
        description=(
            "GUID of the sdkmessage to scope the lookup. "
            "Required when using message+entity mode."
        ),
    )
    primary_entity: str | None = Field(
        default=None,
        description=(
            "Lowercase logical name of the primary entity e.g. 'contact'. "
            "Required when using message+entity mode."
        ),
    )

    @field_validator("filter_id", "message_id")
    @classmethod
    def validate_guids(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_identifier_mode(self) -> "GetSdkMessageFilterInput":
        has_filter_id = bool(self.filter_id)
        has_message_entity = bool(self.message_id) or bool(self.primary_entity)
        if has_filter_id and has_message_entity:
            raise ValueError(
                "Provide either filter_id alone, or message_id + primary_entity — not both modes"
            )
        if not has_filter_id and not has_message_entity:
            raise ValueError(
                "Provide either filter_id, or message_id + primary_entity"
            )
        if has_message_entity and not (self.message_id and self.primary_entity):
            raise ValueError(
                "Both message_id and primary_entity are required when not using filter_id"
            )
        return self


class ListSdkMessageFiltersInput(DataverseEnvironmentInput):
    """Input for listing SDK message filters."""

    message_id: str | None = Field(
        default=None,
        description="GUID of the sdkmessage; return filters for one message only",
    )
    primary_entity: str | None = Field(
        default=None,
        description="Lowercase logical name of the primary entity e.g. 'contact'",
    )
    filter: str | None = Field(
        default=None,
        description="Raw OData $filter expression",
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to sdkmessagefilterid, "
            "primaryobjecttypecode, _sdkmessageid_value."
        ),
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return",
        ge=1,
        le=5000,
    )

    @field_validator("message_id")
    @classmethod
    def validate_message_id(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


# --- F. SDK message processing steps ---


class GetPluginStepInput(DataverseEnvironmentInput):
    """Input for retrieving a single SDK message processing step by ID."""

    step_id: str = Field(
        ...,
        description="GUID of the sdkmessageprocessingstep record to retrieve",
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to sdkmessageprocessingstepid, name, stage, "
            "mode, rank, filteringattributes, statecode, _sdkmessageid_value, "
            "_sdkmessagefilterid_value, _eventhandler_value, description."
        ),
    )

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListPluginStepsInput(DataverseEnvironmentInput):
    """Input for listing SDK message processing steps."""

    plugin_type_id: str | None = Field(
        default=None,
        description="GUID of a plugintype; return only steps handled by that type",
    )
    message_id: str | None = Field(
        default=None,
        description="GUID of a sdkmessage; return only steps for that message",
    )
    filter: str | None = Field(
        default=None,
        description="Raw OData $filter expression",
    )
    select: list[str] | None = Field(
        default=None,
        description="Columns to return. Defaults to the standard step projection.",
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return",
        ge=1,
        le=5000,
    )

    @field_validator("plugin_type_id", "message_id")
    @classmethod
    def validate_guids(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class CreatePluginStepInput(DataverseEnvironmentInput):
    """Input for registering a plug-in step against a message."""

    name: str = Field(
        ...,
        min_length=1,
        description=(
            "Step name e.g. 'MyOrg.Plugins.ContactPlugin: Create of contact'"
        ),
    )
    plugin_type_id: str = Field(
        ...,
        description="GUID of the plugintype handling this step",
    )
    message_id: str = Field(
        ...,
        description="GUID of the sdkmessage; resolve via dataverse_get_sdk_message",
    )
    filter_id: str | None = Field(
        default=None,
        description=(
            "GUID of the sdkmessagefilter to scope to one entity; "
            "omit to register against all entities"
        ),
    )
    stage: int = Field(
        ...,
        description=(
            "Pipeline stage: 10 = pre-validation (outside the DB transaction), "
            "20 = pre-operation (inside the DB transaction, before the main op), "
            "40 = post-operation (after the main op). Not mutable after registration."
        ),
    )
    mode: int = Field(
        ...,
        description=(
            "Execution mode: 0 = synchronous (runs in the caller's transaction), "
            "1 = asynchronous (runs in a background System Job). Not mutable after registration."
        ),
    )
    rank: int = Field(
        default=1,
        ge=1,
        description="Execution order within the stage (lower runs first)",
    )
    filtering_attributes: str | None = Field(
        default=None,
        description=(
            "Comma-separated logical names; step fires only when one of these "
            "attributes changes. Use for Update steps only."
        ),
    )
    supported_deployment: int = Field(
        default=0,
        description="0=Server Only, 1=Outlook Client Only, 2=Both",
    )
    async_auto_delete: bool = Field(
        default=False,
        description=(
            "When True, automatically deletes completed async operations. "
            "Only relevant for asynchronous steps (mode=1)."
        ),
    )
    configuration: str | None = Field(
        default=None,
        description="Unsecure configuration string passed to the plug-in constructor",
    )
    description: str | None = Field(
        default=None,
        description="Optional step description",
    )

    @field_validator("plugin_type_id", "message_id")
    @classmethod
    def validate_required_guids(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @field_validator("filter_id")
    @classmethod
    def validate_filter_id(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @field_validator("stage")
    @classmethod
    def validate_stage(cls, v: int) -> int:
        if v not in (10, 20, 40):
            raise ValueError("stage must be 10 (PreValidation), 20 (PreOperation), or 40 (PostOperation)")
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("mode must be 0 (Synchronous) or 1 (Asynchronous)")
        return v

    @field_validator("supported_deployment")
    @classmethod
    def validate_supported_deployment(cls, v: int) -> int:
        if v not in (0, 1, 2):
            raise ValueError("supported_deployment must be 0 (Server Only), 1 (Outlook Client Only), or 2 (Both)")
        return v


class UpdatePluginStepInput(DataverseEnvironmentInput):
    """Input for updating an SDK message processing step."""

    step_id: str = Field(
        ...,
        description="GUID of the sdkmessageprocessingstep record to update",
    )
    name: str | None = Field(
        default=None,
        description="Updated step name",
    )
    rank: int | None = Field(
        default=None,
        ge=1,
        description="Updated execution order within the stage",
    )
    filtering_attributes: str | None = Field(
        default=None,
        description="Updated comma-separated attribute filter",
    )
    state: str | None = Field(
        default=None,
        description="'enabled' or 'disabled' — use to enable/disable the step without deleting",
    )
    configuration: str | None = Field(
        default=None,
        description="Updated unsecure configuration string",
    )
    description: str | None = Field(
        default=None,
        description="Updated step description",
    )

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str | None) -> str | None:
        if v is not None and v not in ("enabled", "disabled"):
            raise ValueError("state must be 'enabled' or 'disabled'")
        return v

    @model_validator(mode="after")
    def check_has_updates(self) -> "UpdatePluginStepInput":
        if (
            self.name is None
            and self.rank is None
            and self.filtering_attributes is None
            and self.state is None
            and self.configuration is None
            and self.description is None
        ):
            raise ValueError(
                "At least one updatable field must be provided: "
                "name, rank, filtering_attributes, state, configuration, description"
            )
        return self


class DeletePluginStepInput(DataverseEnvironmentInput):
    """Input for deleting an SDK message processing step."""

    step_id: str = Field(
        ...,
        description="GUID of the sdkmessageprocessingstep record to delete",
    )

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


# --- G. SDK message processing step images ---


class GetPluginStepImageInput(DataverseEnvironmentInput):
    """Input for retrieving a single step image by ID."""

    image_id: str = Field(
        ...,
        description="GUID of the sdkmessageprocessingstepimage record to retrieve",
    )
    select: list[str] | None = Field(
        default=None,
        description=(
            "Columns to return. Defaults to sdkmessageprocessingstepimageid, name, "
            "imagetype, entityalias, messagepropertyname, attributes, "
            "_sdkmessageprocessingstepid_value."
        ),
    )

    @field_validator("image_id")
    @classmethod
    def validate_image_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class ListPluginStepImagesInput(DataverseEnvironmentInput):
    """Input for listing step images, optionally scoped to one step."""

    step_id: str | None = Field(
        default=None,
        description="GUID of a sdkmessageprocessingstep; return images for that step only",
    )
    filter: str | None = Field(
        default=None,
        description="Raw OData $filter expression",
    )
    select: list[str] | None = Field(
        default=None,
        description="Columns to return. Defaults to the standard image projection.",
    )
    top: int = Field(
        default=50,
        description="Maximum number of records to return",
        ge=1,
        le=5000,
    )

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, v: str | None) -> str | None:
        if v is not None and not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v


class CreatePluginStepImageInput(DataverseEnvironmentInput):
    """Input for registering a pre/post image on a step."""

    step_id: str = Field(
        ...,
        description="GUID of the parent sdkmessageprocessingstep record",
    )
    image_type: int = Field(
        ...,
        description="0=PreImage, 1=PostImage, 2=Both",
    )
    entity_alias: str = Field(
        ...,
        min_length=1,
        description=(
            "Key used to access the image in the plug-in property bag e.g. 'PreImage'"
        ),
    )
    message_property_name: str = Field(
        ...,
        min_length=1,
        description=(
            "Request message property the image is taken from, "
            "e.g. 'Target' for Create/Update"
        ),
    )
    name: str | None = Field(
        default=None,
        description="Image name; defaults to entity_alias if omitted",
    )
    attributes: str | None = Field(
        default=None,
        description=(
            "Comma-separated logical names to include in the image; "
            "omit for all attributes"
        ),
    )
    description: str | None = Field(
        default=None,
        description="Optional image description",
    )

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @field_validator("image_type")
    @classmethod
    def validate_image_type(cls, v: int) -> int:
        if v not in (0, 1, 2):
            raise ValueError("image_type must be 0 (PreImage), 1 (PostImage), or 2 (Both)")
        return v


class UpdatePluginStepImageInput(DataverseEnvironmentInput):
    """Input for updating a step image record."""

    image_id: str = Field(
        ...,
        description="GUID of the sdkmessageprocessingstepimage record to update",
    )
    entity_alias: str | None = Field(
        default=None,
        description="Updated property bag key",
    )
    message_property_name: str | None = Field(
        default=None,
        description="Updated request message property name",
    )
    attributes: str | None = Field(
        default=None,
        description="Updated comma-separated attribute list",
    )
    name: str | None = Field(
        default=None,
        description="Updated image name",
    )

    @field_validator("image_id")
    @classmethod
    def validate_image_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v

    @model_validator(mode="after")
    def check_has_updates(self) -> "UpdatePluginStepImageInput":
        if (
            self.entity_alias is None
            and self.message_property_name is None
            and self.attributes is None
            and self.name is None
        ):
            raise ValueError(
                "At least one updatable field must be provided: "
                "entity_alias, message_property_name, attributes, name"
            )
        return self


class DeletePluginStepImageInput(DataverseEnvironmentInput):
    """Input for deleting a step image."""

    image_id: str = Field(
        ...,
        description="GUID of the sdkmessageprocessingstepimage record to delete",
    )

    @field_validator("image_id")
    @classmethod
    def validate_image_id(cls, v: str) -> str:
        if not _GUID_PATTERN.match(v):
            raise ValueError(f"Invalid GUID format: '{v}'")
        return v
