param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$OutputDir = (Join-Path $ProjectRoot "dist"),
    [string]$BundleName = "",
    [switch]$IncludeState
)

$ErrorActionPreference = "Stop"

if (-not $BundleName) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $BundleName = if ($IncludeState) {
        "xrp-bot-migration-$stamp"
    } else {
        "xrp-bot-release-$stamp"
    }
}

$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) $BundleName
$payloadRoot = Join-Path $stagingRoot "payload"
$bundlePath = Join-Path $OutputDir "$BundleName.zip"

if (Test-Path $stagingRoot) {
    Remove-Item -Recurse -Force $stagingRoot
}

New-Item -ItemType Directory -Force -Path $payloadRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$exclusions = @(
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "dist"
)

Get-ChildItem -Force $ProjectRoot | Where-Object {
    $name = $_.Name
    if ($exclusions -contains $name) { return $false }
    if (-not $IncludeState -and $name -in @(".env", "data", "logs")) { return $false }
    return $true
} | ForEach-Object {
    Copy-Item $_.FullName -Destination $payloadRoot -Recurse -Force
}

$readme = @"
Bundle: $BundleName
CreatedAt: $(Get-Date -Format s)
ProjectRoot: $ProjectRoot
IncludeState: $IncludeState

Install on Ubuntu:
  unzip $BundleName.zip
  cd payload
  chmod +x deploy/install_bundle.sh
  ./deploy/install_bundle.sh

If IncludeState is false:
  copy .env.example to .env and complete your secrets before starting the service.
"@

Set-Content -Path (Join-Path $stagingRoot "BUNDLE_INFO.txt") -Value $readme -Encoding UTF8

if (Test-Path $bundlePath) {
    Remove-Item -Force $bundlePath
}

Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $bundlePath -CompressionLevel Optimal | Out-Null

Write-Host "Bundle created at: $bundlePath"
