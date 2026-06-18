# ContactPlugin — Sample Dataverse Plug-in

A minimal, no-harm sample plug-in for exercising the Dataverse plug-in registration MCP tools.

**What it does:** writes one trace line via `ITracingService` and mutates nothing.
The only observable effect is a `plugintracelog` row, which the live-validation plan queries
to confirm execution.

---

## Project layout

```
samples/SamplePlugin/
  ContactPlugin.cs       — IPlugin implementation (MyOrg.Plugins.ContactPlugin)
  ContactPlugin.csproj   — Class library, net462, signed at build
  .gitignore             — Excludes generated ContactPlugin.snk and build outputs
  README.md              — This file
```

---

## Requirements

| Dependency | Notes |
|---|---|
| .NET SDK 6+ (or 10) | Cross-compiles to net462 via NuGet reference assemblies |
| Internet access at build | NuGet restore fetches `Microsoft.CrmSdk.CoreAssemblies` and `Microsoft.NETFramework.ReferenceAssemblies.net462` |
| PowerShell | Used by the `GenerateSnk` MSBuild target to create the dev key (built into Windows; install `powershell` on Linux/macOS) |

No .NET Framework SDK installation is required on the build machine — the
`Microsoft.NETFramework.ReferenceAssemblies.net462` NuGet package provides the
reference assemblies needed for cross-compilation.

---

## Strong-name signing (dev key)

Dataverse requires all plug-in assemblies to be strong-name signed.

The `GenerateSnk` MSBuild target (in `ContactPlugin.csproj`) generates a **throwaway dev key**
(`ContactPlugin.snk`) the first time you build, using PowerShell's
`RSACryptoServiceProvider`.  The `.snk` is gitignored — it is never committed.

**Important:** the generated key is for local development and CI only.
For production deployment, generate a key outside source control and supply it
separately via the `AssemblyOriginatorKeyFile` MSBuild property or by placing
it in the project directory before building.

---

## Building

From the repo root (or the `samples/SamplePlugin/` directory):

```bash
dotnet build samples/SamplePlugin/ContactPlugin.csproj -c Release
```

On the first run the `GenerateSnk` target creates `ContactPlugin.snk` automatically.
Subsequent builds reuse the same dev key.

**Output DLL path** (relative to repo root):

```
samples/SamplePlugin/bin/Release/ContactPlugin.dll
```

---

## Base64-encoding the DLL

The `dataverse_create_plugin_assembly` MCP tool (and the raw `POST /pluginassemblies`
Web API call) require the DLL bytes as a Base64-encoded string in the `content` field.

**PowerShell:**

```powershell
$dll  = "samples/SamplePlugin/bin/Release/ContactPlugin.dll"
$b64  = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($dll))
# Pipe to clipboard or save to file:
$b64 | Set-Clipboard
# Or write to a file:
$b64 | Out-File -Encoding ascii contactplugin_b64.txt
```

**Bash (Linux/macOS):**

```bash
base64 -w 0 samples/SamplePlugin/bin/Release/ContactPlugin.dll > contactplugin_b64.txt
```

---

## Registering via Dataverse MCP tools

Use the MCP tools in sequence (or the raw Web API calls in the live-validation plan).
Replace `yourorg` / `your-tenant-id` / `your-client-id` with real values from your
environment — **never hardcode credentials**.

### 1. Upload the assembly

```json
{
  "tool": "dataverse_create_plugin_assembly",
  "name": "ContactPlugin",
  "content": "<base64 string from above>",
  "isolation_mode": 2,
  "dataverse_url": "https://yourorg.crm.dynamics.com"
}
```

Returns `assembly_id` (GUID).

### 2. Register the plug-in type

```json
{
  "tool": "dataverse_create_plugin_type",
  "assembly_id": "<assembly_id from step 1>",
  "typename": "MyOrg.Plugins.ContactPlugin",
  "friendly_name": "Contact Plugin",
  "dataverse_url": "https://yourorg.crm.dynamics.com"
}
```

Returns `plugin_type_id` (GUID).

### 3. Resolve the message GUID

```json
{
  "tool": "dataverse_get_sdk_message",
  "message_name": "Create",
  "dataverse_url": "https://yourorg.crm.dynamics.com"
}
```

Returns `sdkmessageid` (GUID) — capture as `message_id`.

### 4. Resolve the message filter GUID (scope to `contact`)

```json
{
  "tool": "dataverse_get_sdk_message_filter",
  "message_id": "<message_id from step 3>",
  "primary_entity": "contact",
  "dataverse_url": "https://yourorg.crm.dynamics.com"
}
```

Returns `sdkmessagefilterid` (GUID) — capture as `filter_id`.

### 5. Create the processing step

```json
{
  "tool": "dataverse_create_plugin_step",
  "name": "MyOrg.Plugins.ContactPlugin: Create of contact",
  "plugin_type_id": "<plugin_type_id from step 2>",
  "message_id": "<message_id from step 3>",
  "filter_id": "<filter_id from step 4>",
  "stage": 40,
  "mode": 0,
  "rank": 1,
  "dataverse_url": "https://yourorg.crm.dynamics.com"
}
```

Returns `step_id` (GUID).

### Verify it fired

After creating a contact record, query the plug-in trace logs:

```json
{
  "tool": "dataverse_list_plugin_trace_logs",
  "type_name": "MyOrg.Plugins.ContactPlugin",
  "dataverse_url": "https://yourorg.crm.dynamics.com"
}
```

Expect at least one row with `messagename = "Create"` and `primaryentity = "contact"`.

---

## Teardown (reverse order)

```
dataverse_delete_plugin_step      step_id
dataverse_delete_plugin_type      plugin_type_id
dataverse_delete_plugin_assembly  assembly_id
```

---

## Notes

- **Isolation mode 2 (Sandbox)** is required for Dataverse online — the default.
- **Stage 40 (Post-operation), Mode 0 (Synchronous)** is the safest demonstration
  configuration: the contact record already exists before the plug-in runs, and the
  trace log appears immediately.
- The plug-in writes no data — it is safe to register and trigger in any sandbox environment.
