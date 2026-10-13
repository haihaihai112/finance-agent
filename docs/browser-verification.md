# 审阅修订浏览器验收

`tests/browser/review-revisions.cjs` 使用 Playwright 驱动真实浏览器，服务首页和 API 同源。安装项目开发依赖和 Playwright（或使用已有模块），浏览器需已安装。默认浏览器为 Edge，默认 URL 为 `http://127.0.0.1:8789`。

服务与测试分别在两个终端运行。PowerShell 示例（在项目目录内）：

```powershell
$env:LLM_ENABLED = 'false'
$env:MCP_TRANSPORT = 'inprocess'
$env:FINANCE_DATA_DIR = (Join-Path (Get-Location) 'work/browser-review-runtime')
$env:DATABASE_URL = 'sqlite+pysqlite:///' + (Join-Path $env:FINANCE_DATA_DIR 'finance.db').Replace('\', '/')
.venv/Scripts/python.exe -m uvicorn finance_agents.api.app:app --host 127.0.0.1 --port 8789
```

另一个终端：

```powershell
# 如已在本项目安装 playwright，可以不设置 PLAYWRIGHT_MODULE。
$env:PLAYWRIGHT_MODULE = '<已有 Playwright 模块目录>'
$env:PLAYWRIGHT_CHANNEL = 'msedge'
$env:FINANCE_BASE_URL = 'http://127.0.0.1:8789'
$env:BROWSER_CASE = 'all'
$env:BROWSER_SCREENSHOT_DIR = 'work/browser-review-screenshots'
node tests/browser/review-revisions.cjs
```

`PLAYWRIGHT_MODULE` 可使用包名或模块目录；没有机器专用的绝对 import。`PLAYWRIGHT_CHANNEL` 可设为 `chrome`；`BROWSER_HEADLESS=false` 显示浏览器；`BROWSER_CASE=fixtures` 或 `loan` 单独执行一组。`BROWSER_SCREENSHOT_DIR` 可省略。测试结束后在服务终端按 Ctrl+C 停止服务。

## 验收范围

- 四种理赔显示为受控响应夹具：`potentially_covered`、`outside_coverage_dates`、`excluded_by_provided_clause`、`requires_manual_clause_review`。断言中文摘要、日期与 `within_effective_dates` 展示；这些案例不是四种业务条款的端到端验证。
- 贷款流程使用真实本地 API、真实存储与合成 PDF，无 HTTP 夹具：逐项确认 → 显式授权 → 完成 → 刷新 → 点击复制 → 读取服务端材料和证据 → 确认框全部为空、无授权 → 未勾选时阻止提交 → 改金额 12000 为 24000、期限 12 为 24 → 再确认 → 授权前无评估结果 → 新授权 → 完成。
- 验证新运行具有不同 ID、`parent_run_id` 指向原运行、材料和原始证据一致、新授权引用不同；原运行表单、结果和授权引用完全保留。读取原历史结论，并检查浏览器脚本错误。
- 测试会创建新会话和合成资料，应使用专用本地数据库。此流程仍使用模拟贷款规则，不代表真实金融机构接入、生产性能或 OCR 准确率验证。

## 执行记录

2026-10-13：Edge / Playwright、规则模式、独立 SQLite、进程内 MCP。四种受控理赔显示通过；贷款复制验收记录在本次实现报告中，未执行前不标记通过。
