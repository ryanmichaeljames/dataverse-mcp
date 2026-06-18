# generate-snk.ps1
# Generates a throwaway dev strong-name key (ContactPlugin.snk).
# WARNING: DEV-ONLY key. Do NOT commit to source control.
#          Do NOT use for production deployment.
#
# Called by the GenerateSnk MSBuild target in ContactPlugin.csproj.
# Also callable standalone:
#   powershell -NoProfile -ExecutionPolicy Bypass -File generate-snk.ps1

param(
    [string]$OutFile = (Join-Path $PSScriptRoot "ContactPlugin.snk")
)

if (Test-Path $OutFile) {
    Write-Host "generate-snk.ps1: $OutFile already exists - skipping."
    exit 0
}

Add-Type -AssemblyName 'System.Security'
$csp   = New-Object System.Security.Cryptography.RSACryptoServiceProvider 1024
$bytes = $csp.ExportCspBlob($true)
[System.IO.File]::WriteAllBytes($OutFile, $bytes)
Write-Host "generate-snk.ps1: generated throwaway dev key at $OutFile"
