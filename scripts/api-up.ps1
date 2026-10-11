[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$Model = "build/demo/model/baseline.json",
    [string]$Predictions = "build/demo/predictions/candidate.ndjson"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = if ($env:PYTHON) {
    $env:PYTHON
}
elseif (Test-Path -LiteralPath (Join-Path $repoRoot ".venv\Scripts\python.exe")) {
    Join-Path $repoRoot ".venv\Scripts\python.exe"
}
elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
    "py"
}
else {
    "python"
}
$pythonArguments = if ($pythonExecutable -eq "py") { @("-3") } else { @() }
Push-Location $repoRoot
try {
    $env:CHURN_MODEL_PATH = $Model
    $env:CHURN_PREDICTIONS_PATH = $Predictions
    & $pythonExecutable @pythonArguments -m uvicorn `
        churn_platform.api:app --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
