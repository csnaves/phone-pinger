# Run a dev instance of the locator against a separate iCloud account.
#
#   powershell -ExecutionPolicy Bypass -File .\dev.ps1 [-Port 8001] [-EnvFile dev.env]
#
# Reads ICLOUD_EMAIL, ICLOUD_PASSWORD, ICLOUD_DEVICE_ID and LOCATOR_TOKEN from the
# env file (copy dev.env.example). The machine-wide production values are always
# present in new shells, so this refuses to start if any value is missing, still a
# placeholder, or matches production. Binds to 127.0.0.1 only and auto-reloads on
# code changes. The shell's environment is restored on exit.
param(
    [int]$Port = 8001,
    [string]$EnvFile = "dev.env"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Required = "ICLOUD_EMAIL", "ICLOUD_PASSWORD", "ICLOUD_DEVICE_ID", "LOCATOR_TOKEN"

function Fail([string]$msg) {
    Write-Host "dev.ps1: $msg" -ForegroundColor Red
    exit 1
}

if ($Port -eq 8000) { Fail "port 8000 belongs to LocatorService; pick another." }
if (-not (Test-Path $EnvFile)) { Fail "$EnvFile not found. Copy dev.env.example to $EnvFile and fill it in." }

$values = @{}
foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
    $line = $line.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { continue }
    $key, $value = $line -split "=", 2
    $values[$key.Trim()] = "$value".Trim()
}

foreach ($key in $Required) {
    $value = $values[$key]
    if (-not $value) { Fail "$key is missing or empty in $EnvFile." }
    if ($value.StartsWith("<")) { Fail "$key in $EnvFile is still a placeholder." }
}

# Guard against the dev instance quietly using the production account or phone.
foreach ($key in "ICLOUD_EMAIL", "ICLOUD_DEVICE_ID", "LOCATOR_TOKEN") {
    $prod = [Environment]::GetEnvironmentVariable($key, "Machine")
    if ($prod -and $values[$key] -ieq $prod) { Fail "$key in $EnvFile matches production. Use a different value." }
}

$saved = @{}
foreach ($key in $Required) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
    [Environment]::SetEnvironmentVariable($key, $values[$key], "Process")
}

try {
    Write-Host "Dev instance for $($values['ICLOUD_EMAIL']) on port $Port" -ForegroundColor Cyan
    Write-Host "  2FA page: http://localhost:$Port/icloud?token=$($values['LOCATOR_TOKEN'])"
    Write-Host "  Ping:     curl.exe -X POST -H `"Authorization: Bearer <token>`" http://localhost:$Port/ping"
    & .\venv\Scripts\python.exe -m uvicorn locator:app --host 127.0.0.1 --port $Port --reload
}
finally {
    foreach ($key in $Required) { [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process") }
}
