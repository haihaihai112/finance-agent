param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
if (!$Python) { $Python = Join-Path $projectPath '.venv/Scripts/python.exe' }
if (!(Test-Path -LiteralPath $Python)) { throw '找不到解释器，请创建 .venv 或传入 -Python 绝对路径。' }
Push-Location -LiteralPath $projectPath
try {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'pytest 验收失败。' }
    & $Python -m ruff check src tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'ruff check 验收失败。' }
    & $Python -m ruff format --check src tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'ruff format 验收失败。' }
    & $Python -m compileall -q src scripts
    if ($LASTEXITCODE -ne 0) { throw 'compileall 验收失败。' }
    Write-Output '本地代码验收通过；外部服务和浏览器结果请另查对应验收记录。'
}
finally { Pop-Location }
