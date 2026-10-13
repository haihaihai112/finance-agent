# 金融多智能体个人项目

这是可本地运行的个人项目，支持投资咨询、理赔申请、贷款申请、政策／税务及其他五类意图。六个 Agent 用 LangGraph 分工，四个 MCP 服务通过 SDK 的 stdio 协议调用。提供浏览器工作台、文件上传、OCR、表单确认、授权、证据引用和运行恢复。

首版范围：中国大陆、人民币、公开日频基金／ETF单位净值、居民年度综合所得个税。保险示例与征信／风控明确使用模拟数据；配置是透明的类别模板，不预测收益。

## 在 VS Code 启动

在此文件夹打开终端，使用 Python 3.11 及以上（本次验证环境为 3.13）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn finance_agents.api.app:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 查看工作台，http://127.0.0.1:8000/docs 查看接口；也可以选择 `.venv` 解释器后按 F5。`scripts/start.ps1` 提供同样的启动入口。退出服务按 Ctrl+C。

DeepSeek 密钥填入本地 `.env` 的 `DEEPSEEK_API_KEY`，不要提交密钥。无密钥时意图路由降级为规则，结果保持确定性输出。`DEEPSEEK_MODEL` 可配置，当前默认来自验证时官方文档的 `deepseek-flash`；模型名称与账号可用性请以你的接口为准。真实付费调用尚未验证。

默认 SQLite，无需启动数据库服务。切换 MySQL 见 [数据库说明](docs/mysql.md)。默认 `MCP_TRANSPORT=stdio`，每次工具调用启动独立服务并再次验证身份、运行和授权；`inprocess` 用相同业务与校验代码快速调试，但不经过协议传输。

## 演示流程

1. 创建本地会话，再点“导入演示资料”。演示保单与收入 PDF 明确标为模拟，资料不会自动附加到请求。此操作也导入带来源和采集时间的真实 510300 净值快照。
2. 投资：填写并确认可投资金额、期限、风险程度、现金需求、负债、现有资产、地域、基金代码。无负债明确填 0，无资产确认空列表；使用 510300 查看快照历史指标。
3. 理赔：选择演示保单 PDF，上传并确认 `DEMO-POLICY-001 / demo-v1`、`hospitalization`、`2026-10-01`；查询当前用户的历史保单与条款并展示初步筛查。
4. 贷款：选择收入 PDF，核对月收入与负债，补充申请人编号、申请金额、期限及就业状态；逐项确认、单独授权，再继续评估。结果明确为模拟规则，可撤销授权。
5. 税务：大陆居民年度综合所得收入额 120000 元、60000 元之外年度扣除 0 元、税年 2026，确认后得到年度税额 3480.00 元；此金额未减预缴税额，不等于补税／退税。

OCR 使用 RapidOCR ONNX：原生 PDF 优先取文字层，图片和扫描页实际推理，字段保留页码、原文、坐标及置信度。金融字段不会自动确认。复杂表格、手写票据和混合页面识别仍需扩展，当前没有代表性准确率结论，见 [OCR验证](docs/ocr-verification.md)。

## 数据采集与更新

```powershell
.\.venv\Scripts\python.exe -m finance_agents.data_jobs.scheduler market --instruments 510300
.\.venv\Scripts\python.exe -m finance_agents.data_jobs.scheduler policy --check-live
```

采集器读同一 `.env` 数据库。行情更新采用受控公开来源、分页校验、重叠窗口、事务 upsert、水位与修订审计；失败不推进水位。政策监测固定官方来源的内容 hash，变化须人工审核后导入版本，不自动把网页变化解释成新税法。Windows 任务计划程序配置见 [采集说明](docs/market-ingestion.md)。

快照截至 2026-10-13，为日频单位净值，不是交易所实时 ETF 报价；超过新鲜度窗口不会继续返回投资指标，应先更新数据。公开接口不可用时返回不可用，不补造价格。来源核验见 [数据验证](docs/data-source-validation.md)。

## 结构与验证

`src/finance_agents/` 下的 `agents` 放六个业务 Agent，`workflow` 放图和持久化门禁，`mcp_services` 放四服务及协议，`tools` 放 OCR／表单／检索，`storage` 放 SQL 仓储，`data_jobs` 放采集；`web` 是无构建步骤的工作台。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\ruff.exe check src tests
```

业务检查点保存到 SQL，补答按原业务重入 LangGraph；没有使用 LangGraph 原生 `interrupt`／checkpointer。请求支持幂等和状态版本校验，会话／文档／运行按用户隔离；本地会话是演示身份机制，尚未接企业 SSO 或真实银行审批。

[架构](docs/architecture.md) · [项目简介与面试亮点](docs/project-highlights.md) · [工作台使用](docs/workbench-guide.md) · [验收记录](docs/verification.md)

MySQL真实服务和 DeepSeek 真实密钥调用须在相应环境验证。当前不声称生产级金融准确性、OCR准确率、吞吐量或业务收益；真实／模拟模式与来源随结果保留。GitHub账号已记录，当前没有创建、推送或发布仓库。

如需复现本次干净Windows/Python3.13依赖，可安装 `requirements-lock-windows-py313.txt` 后再 `pip install -e . --no-deps`。该锁文件不用于其他Python版本或系统；通用安装仍以pyproject.toml为准。

当前仅提供公开日频净值；若后续需要实时交易行情，应另接获许可的报价源、定义更新时间与过期规则，不能把本快照包装为实时服务。

入口默认“自动识别”，会调用DeepSeek或明确规则降级；手动指定五类业务时提供intent_hint。多业务问题先澄清，表单按服务端实际意图展示，不把自动识别作为第六意图。
