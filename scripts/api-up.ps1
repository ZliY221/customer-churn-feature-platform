[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$Model = "build/demo/model/baseline.json",
    [string]$Predictions = "build/demo/predictions/candidate.ndjson",
    [string]$TaskDatabase = "build/operations/retention-tasks.sqlite3"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "python-runtime.ps1")
$pythonRuntime = Resolve-ProjectPython -MinimumVersion "3.11" -RepoRoot $repoRoot
$pythonExecutable = $pythonRuntime.Executable
$pythonArguments = @($pythonRuntime.Arguments)
Push-Location $repoRoot
try {
    $env:CHURN_MODEL_PATH = $Model
    $env:CHURN_PREDICTIONS_PATH = $Predictions
    $env:CHURN_TASK_DATABASE_PATH = $TaskDatabase
    & $pythonExecutable @pythonArguments -m uvicorn `
        churn_platform.api:app --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
