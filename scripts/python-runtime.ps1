function Resolve-ProjectPython {
    [CmdletBinding()]
    param(
        [Version]$MinimumVersion = [Version]"3.11",
        [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
    )

    $candidates = @()
    if ($env:PYTHON) { $candidates += $env:PYTHON }
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) { $candidates += $venvPython }
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $candidates += @("py", "python", "python3")
    }
    else {
        $candidates += @("python3", "python", "py")
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
        $candidateArguments = @()
        if ($candidate -eq "py") { $candidateArguments += "-3" }
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $candidate @candidateArguments -c `
                "import sys; raise SystemExit(0 if sys.version_info >= ($($MinimumVersion.Major), $($MinimumVersion.Minor)) else 1)" *> $null
            $exitCode = $LASTEXITCODE
        }
        catch {
            $exitCode = 1
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($exitCode -eq 0) {
            return [pscustomobject]@{
                Executable = $candidate
                Arguments = [string[]]$candidateArguments
            }
        }
    }

    throw "Python $MinimumVersion or newer was not found. Set the PYTHON environment variable if needed."
}
