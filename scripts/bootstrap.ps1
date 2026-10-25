# Install the locked Python environment without changing global Python or starting services.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonRoot = Join-Path $projectRoot '.agent-work/python'
$previousPythonRoot = $env:UV_PYTHON_INSTALL_DIR
$previousEnvironment = $env:UV_PROJECT_ENVIRONMENT

try {
    $uvVersion = & uv --version
    if ($LASTEXITCODE -ne 0 -or $uvVersion -notmatch '^uv 0\.11\.19(?:\s|$)') {
        throw 'Bootstrap requires uv 0.11.19; install that version separately. No global installs are performed here.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'uv.lock'))) {
        throw 'uv.lock is required; bootstrap never resolves an unlocked environment.'
    }
    $env:UV_PYTHON_INSTALL_DIR = $pythonRoot
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $projectRoot '.venv'
    & uv python install 3.12.13 --install-dir $pythonRoot --no-bin --no-registry
    if ($LASTEXITCODE -ne 0) { throw 'Local Python installation failed.' }
    $pythonExecutable = & uv python find 3.12.13 --managed-python --no-python-downloads
    if ($LASTEXITCODE -ne 0) { throw 'Local Python discovery failed.' }
    $resolvedPython = [IO.Path]::GetFullPath($pythonExecutable.Trim())
    $expectedRoot = [IO.Path]::GetFullPath($pythonRoot) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPython.StartsWith($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Discovered interpreter is outside this project; refusing global Python.'
    }
    & uv sync --project $projectRoot --locked --python $resolvedPython --extra test --extra validation --no-python-downloads
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    Write-Output "Ready: $projectRoot/.venv (Python 3.12.13; test + validation extras)."
    Write-Output 'Browser binaries, listeners and provider calls were not started.'
}
finally {
    $env:UV_PYTHON_INSTALL_DIR = $previousPythonRoot
    $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
}
