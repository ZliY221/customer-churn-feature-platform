[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$previousPythonPath = $env:PYTHONPATH
. (Join-Path $PSScriptRoot "python-runtime.ps1")
$pythonRuntime = Resolve-ProjectPython -MinimumVersion "3.11" -RepoRoot $repoRoot
$pythonExecutable = $pythonRuntime.Executable
$pythonArguments = @($pythonRuntime.Arguments)
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
