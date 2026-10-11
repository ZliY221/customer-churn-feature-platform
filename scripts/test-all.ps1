[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $repoRoot "src"
    py -3 -W error -m unittest discover -s (Join-Path $repoRoot "tests") -v
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed." }
    py -3 (Join-Path $PSScriptRoot "audit-publication.py")
    if ($LASTEXITCODE -ne 0) { throw "Publication audit failed." }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
