# OData Patterns — Dataverse Gotchas

Reference for non-obvious Dataverse-specific OData behaviour.

---

## $filter — Dataverse-specific rules

**Lookup fields:** filter on `_fieldname_value`, not the navigation property name:
```
_parentaccountid_value eq xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```
GUIDs are unquoted in filter expressions.

**Choice columns:** filter on the integer value, not the label:
```
prioritycode eq 1
industrycode eq 6
```

**Active-only records:** most tables use `statecode eq 0` (active) / `1` (inactive):
```
filter="statecode eq 0 and prioritycode eq 1"
```

**Escape single quotes** in string literals by doubling them:
```
filter="lastname eq 'O''Brien'"
```

---

## $expand — navigation properties

Navigation property names come from the relationship schema name. Use
`dataverse_list_relationships` to find them. Nested `$select` and `$top` are
supported inside the expand:

```
expand="parentaccountid($select=name,accountid)"
expand="contact_customer_accounts($select=fullname;$top=10)"
```

Max **15 `$expand`** options per query. Nested `$orderby` inside `$expand` is NOT
supported by Dataverse.

---

## Formatted values

Set `include_formatted_values=True` on `query_table` or `get_record` to get
human-readable labels alongside raw values. Useful for choice columns, dates,
and lookup display names. Formatted values appear as:
```
"statecode@OData.Community.Display.V1.FormattedValue": "Active"
"_ownerid_value@OData.Community.Display.V1.FormattedValue": "Ryan James"
```

---

## Counting records

- `dataverse_count_records` — returns integer count (capped at 5,000)
- `dataverse_query_table` with `count=True` — fetches records AND total count
- If `total_count == 5000` the actual count may be higher

---

## Aggregation ($apply)

Use `dataverse_aggregate_table` for `$apply` expressions. Works on up to 50,000
records. `$orderby` on aggregate alias values is NOT supported by Dataverse.

Common patterns:
```
# Count by status
groupby((statecode),aggregate(accountid with count as total))

# Sum
aggregate(revenue with sum as total_revenue)

# Distinct values
groupby((ownerid))
```

---

## Lookup column naming

When selecting a lookup column, two fields are available:
- `_fieldname_value` — GUID of the related record
- `_fieldname_value@OData.Community.Display.V1.FormattedValue` — display name

---

## Do NOT URL-encode filter strings

Pass `filter` as a plain string. The server handles encoding. Manually encoding
`$` as `%24` breaks the query.
