[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$previousPythonPath = $env:PYTHONPATH
$pythonExecutable = $null
$pythonArguments = @()
foreach ($candidate in @("python", "python3", "py")) {
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
try {
    $env:PYTHONPATH = Join-Path $repoRoot "src"
    & $pythonExecutable @pythonArguments `
        -W error -m unittest discover -s (Join-Path $repoRoot "tests") -v
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed." }
    & $pythonExecutable @pythonArguments (Join-Path $PSScriptRoot "audit-publication.py")
    if ($LASTEXITCODE -ne 0) { throw "Publication audit failed." }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
