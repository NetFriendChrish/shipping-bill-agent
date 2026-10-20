$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run setup.cmd first.' }
Write-Host 'Open http://127.0.0.1:8765 in your browser. Press Ctrl+C to stop.' -ForegroundColor Green
& $pythonPath -m uvicorn shipping_agent.api:app --host 127.0.0.1 --port 8765
if ($LASTEXITCODE -ne 0) { throw 'Server failed to start. Check the message above.' }
