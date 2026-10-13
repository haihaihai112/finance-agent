# 审阅修订浏览器验收

`tests/browser/review-revisions.cjs` 使用 Playwright 驱动真实浏览器，服务首页和 API 同源。安装项目开发依赖和 Playwright（或使用已有模块），浏览器需已安装。默认浏览器为 Edge，默认 URL 为 `http://127.0.0.1:8789`。

服务与测试分别在两个终端运行。PowerShell 示例（在项目目录内）：

```powershell
$env:LLM_ENABLED = 'false'
$env:MCP_TRANSPORT = 'stdio'
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

`PLAYWRIGHT_MODULE` 可使用包名或模块目录；没有机器专用的绝对 import。`PLAYWRIGHT_CHANNEL` 可设为 `chrome`；`BROWSER_HEADLESS=false` 显示浏览器；`BROWSER_CASE=fixtures`、`restore` 或 `loan` 单独执行一组。`BROWSER_SCREENSHOT_DIR` 可省略。测试结束后在服务终端按 Ctrl+C 停止服务。

## 验收范围

- 四种理赔显示为受控响应夹具：`potentially_covered`、`outside_coverage_dates`、`excluded_by_provided_clause`、`requires_manual_clause_review`。断言中文摘要、日期与 `within_effective_dates` 展示；这些案例不是四种业务条款的端到端验证。
- 两种资料恢复失败为受控网络夹具：运行 A 具有选中附件，读取 B 时让 B 的元信息或提取请求返回 503。断言当前运行、材料和证据仍为 A；随后补答只发往 A，禁止向 B 提交 A 的附件。这些案例不表示真实 OCR 服务故障。
- 贷款流程使用真实本地 API、真实存储与合成 PDF，无 HTTP 夹具：逐项确认 → 显式授权 → 完成 → 刷新 → 点击复制 → 读取服务端材料和证据 → 确认框全部为空、无授权 → 未勾选时阻止提交 → 改金额 12000 为 24000、期限 12 为 24 → 再确认 → 授权前无评估结果 → 新授权 → 完成。
- 验证新运行具有不同 ID、`parent_run_id` 指向原运行、材料和原始证据一致、新授权引用不同；原运行表单、结果和授权引用完全保留。读取原历史结论，并检查浏览器脚本错误。
- 测试会创建新会话和合成资料，应使用专用本地数据库。此流程仍使用模拟贷款规则，不代表真实金融机构接入、生产性能或 OCR 准确率验证。

## 执行记录

2026-10-13：Edge / Playwright、规则模式、独立 SQLite、stdio MCP、隔离 Python 环境。执行 `BROWSER_CASE=all`，四种受控理赔显示、元信息／提取恢复失败夹具以及真实本地 API 贷款复制和重新确认／新授权全部通过。贷款样例从 12000 元／12 个月改为 24000 元／24 个月，原运行未改变，无浏览器脚本错误。

## 个人项目交付脚本

沿用上面的独立服务与环境变量，再执行：

```powershell
node tests/browser/personal-delivery.cjs
```

此脚本不使用BROWSER_CASE，默认覆盖1440px和390px的工作台历史、事件、报告及请求恢复。需要同样的Playwright模块与已安装Edge；默认地址为8791，显式FINANCE_BASE_URL可覆盖，建议沿用上例8789。测试不输出Token、不保存截图或下载中的凭据。

网络恢复分别覆盖：真实服务器处理后丢响应，以及发送到服务器前中断且随后真实404。后一场景必须明确确认“解除本地提交锁定”，不自动重试，不取消服务端请求，保留编号以便追查迟到处理。另有受控404/503/409、附件读取失败和大金额导出夹具；实际税务表单确认、3480.00计算及JSON/Markdown下载使用真实本地API。所有模拟案例与实际本地API案例在日志分别说明。

2026-10-13晚间最终执行：两个脚本均退出0。服务由实际start.ps1在8794启动，使用隔离SQLite、stdio及规则模式；原贷款复制回归和新增交付/断网解除流程全部通过，无页面脚本错误，390px无横向溢出。此结果不构成真实DeepSeek/MySQL或金融业务效果验证。
