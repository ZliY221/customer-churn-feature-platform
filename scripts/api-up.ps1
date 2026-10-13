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
$pythonExecutable = $null
$pythonArguments = @()
$candidates = @()
if ($env:PYTHON) { $candidates += $env:PYTHON }
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) { $candidates += $venvPython }
$candidates += @("python", "python3", "py")
foreach ($candidate in $candidates) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $candidateArguments = @()
        if ($candidate -eq "py") { $candidateArguments = @("-3") }
        & $candidate @candidateArguments -c `
            "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonExecutable = $candidate
            $pythonArguments = $candidateArguments
            break
        }
    }
}
if (-not $pythonExecutable) { throw "Python 3.11 or newer was not found." }
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
