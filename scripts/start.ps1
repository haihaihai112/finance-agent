param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { throw '请先按README创建.venv并安装依赖。' }
Push-Location -LiteralPath $projectPath
try { & $pythonPath -m uvicorn finance_agents.api.app:app --host 127.0.0.1 --port $Port }
finally { Pop-Location }
