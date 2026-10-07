param([int]$Port = 8795)
$ErrorActionPreference = 'Stop'
$grainworksRoot = $PSScriptRoot
Set-Location -LiteralPath $grainworksRoot
$grainworksPython = Join-Path $grainworksRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $grainworksPython)) { throw 'Run ./setup.ps1 first.' }
Write-Host "Grainworks Agent: http://127.0.0.1:$Port"
Write-Host 'Keep this terminal open. Ctrl+C stops the demo. Ollama must run separately.'
& $grainworksPython -X utf8 (Join-Path $grainworksRoot 'server.py') --port $Port --db (Join-Path $grainworksRoot 'work/grainworks.sqlite3')
