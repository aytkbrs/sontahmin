param(
    [ValidateSet("daily", "live-scores")]
    [string]$Mode = "daily"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = (Get-Command python).Source

function Run-Step {
    param(
        [string]$Command
    )

    Write-Host "Running: $Command"
    Push-Location $projectRoot
    try {
        & $pythonExe -m src.iddaa_ingest.cli $Command
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed: $Command"
        }
    }
    finally {
        Pop-Location
    }
}

if ($Mode -eq "daily") {
    Run-Step "fetch-prematch-football"
    Run-Step "fetch-live-football"
}

Run-Step "fetch-live-scores"
Run-Step "generate-labels"
Run-Step "build-training-dataset"
