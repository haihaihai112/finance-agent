param([ValidateRange(1, 65535)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { throw '请先按README创建.venv并安装依赖。' }
Push-Location -LiteralPath $projectPath
try {
    & $pythonPath scripts/doctor.py --port $Port
    if ($LASTEXITCODE -ne 0) { throw '启动诊断存在阻断项，请按提示修复。' }
    Write-Output "服务启动后访问 http://127.0.0.1:$Port；接口文档 http://127.0.0.1:$Port/docs；Ctrl+C 停止。"
    & $pythonPath -m uvicorn finance_agents.api.app:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw '应用服务退出异常。' }
}
finally { Pop-Location }
