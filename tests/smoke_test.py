"""Quick smoke test for all Dataverse MCP tools against a live environment."""

import json
import os
import sys

from dataverse_mcp.client import _build_credential
from PowerPlatform.Dataverse.client import DataverseClient

url = os.environ.get("DATAVERSE_URL")
if not url:
    print("ERROR: Set DATAVERSE_URL env var")
    sys.exit(1)

cred = _build_credential(os.environ.get("DATAVERSE_AUTH_TYPE", "azure_cli"))
client = DataverseClient(url, cred)
print(f"Connected to {url}\n")


def flatten(pages, limit=5):
    records = []
    for page in pages:
        for r in page:
            records.append(dict(r))
            if len(records) >= limit:
                return records
    return records


# --- Test 1: List solutions ---
print("=== 1. dataverse_list_solutions (top 3) ===")
pages = client.records.get(
    "solution",
    select=["solutionid", "uniquename", "friendlyname", "version", "ismanaged"],
    top=3,
)
solutions = flatten(pages, 3)
print(json.dumps(solutions, indent=2, default=str))
print(f"Count: {len(solutions)}\n")

# --- Test 2: Get solution by unique name ---
print("=== 2. dataverse_get_solution (by name) ===")
first_name = solutions[0]["uniquename"]
pages = client.records.get(
    "solution",
    select=["solutionid", "uniquename", "friendlyname", "version"],
    filter=f"uniquename eq '{first_name}'",
    top=1,
)
result = flatten(pages, 1)
print(json.dumps(result, indent=2, default=str))
sol_id = result[0]["solutionid"]
print(f"Solution ID: {sol_id}\n")

# --- Test 3: Get solution by ID ---
print("=== 3. dataverse_get_solution (by ID) ===")
record = client.records.get(
    "solution",
    record_id=sol_id,
    select=["solutionid", "uniquename", "friendlyname", "version"],
)
print(json.dumps(dict(record), indent=2, default=str))
print()

# --- Test 4: List solution components ---
print("=== 4. dataverse_list_solution_components (top 5) ===")
pages = client.records.get(
    "solutioncomponent",
    select=["solutioncomponentid", "componenttype", "objectid"],
    filter=f"_solutionid_value eq '{sol_id}'",
    top=5,
)
comps = flatten(pages, 5)
print(json.dumps(comps, indent=2, default=str))
print(f"Count: {len(comps)}\n")

# --- Test 5: Query table ---
print("=== 5. dataverse_query_table (account, top 3) ===")
pages = client.records.get(
    "account",
    select=["accountid", "name"],
    top=3,
)
accounts = flatten(pages, 3)
print(json.dumps(accounts, indent=2, default=str))
print(f"Count: {len(accounts)}\n")

# --- Test 6: Get single record ---
if accounts:
    acc_id = accounts[0]["accountid"]
    print(f"=== 6. dataverse_get_record (account {acc_id}) ===")
    rec = client.records.get(
        "account",
        record_id=acc_id,
        select=["accountid", "name"],
    )
    print(json.dumps(dict(rec), indent=2, default=str))
    print()
else:
    print("=== 6. SKIPPED (no accounts found) ===\n")

# --- Test 7: List tables ---
print("=== 7. dataverse_list_tables (custom only, first 5) ===")
tables = client.tables.list(
    filter="IsCustomEntity eq true",
    select=["LogicalName", "SchemaName", "DisplayName", "EntitySetName"],
)
print(json.dumps(tables[:5], indent=2, default=str))
print(f"Total custom tables: {len(tables)}\n")

# --- Test 8: Get table metadata ---
print("=== 8. dataverse_get_table_metadata (account) ===")
info = client.tables.get("account")
if info:
    print(f"  logical_name: {info.logical_name}")
    print(f"  schema_name: {info.schema_name}")
    print(f"  entity_set_name: {info.entity_set_name}")
    print(f"  primary_id_attribute: {info.primary_id_attribute}")
    print(f"  primary_name_attribute: {info.primary_name_attribute}")
else:
    print("  Table not found!")

print("\n=== ALL TESTS PASSED ===")
