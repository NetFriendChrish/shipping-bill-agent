$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if ($launcher) { & $launcher.Source -3 -m venv .venv }
    elseif ($pythonCommand) { & $pythonCommand.Source -m venv .venv }
    elseif (Test-Path -LiteralPath $bundledPython) { & $bundledPython -m venv .venv }
    else { throw 'Install Python 3.11+ first: https://www.python.org/downloads/' }
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python virtual environment.' }
}
& $pythonPath -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ required.' }
& $pythonPath -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network and rerun setup.cmd.' }
Write-Host 'Setup complete. Run start.cmd, then open http://127.0.0.1:8765' -ForegroundColor Green
