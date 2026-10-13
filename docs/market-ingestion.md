# 采集、更新与入库

实现：data_jobs/sources.py固定东方财富基金历史净值来源，pipeline.py负责范围校验与增量窗口，storage/market_store.py执行SQL事务。请求代码限定六位基金代码与固定域名，不接受用户任意URL。

```powershell
.\.venv\Scripts\python.exe -m finance_agents.data_jobs.scheduler market --instruments 510300
.\.venv\Scripts\python.exe -m finance_agents.data_jobs.scheduler policy --check-live
```

入口读Settings/.env，默认与API使用同一数据库；可用--database-url显式覆盖。市场代码由参数或MARKET_INSTRUMENT_IDS指定，不自动选购基金。

| 表 | 作用 |
|---|---|
| market_records | source_id+instrument_id+trade_date+record_type唯一键，净值Decimal、币种、来源、采集时间与hash |
| market_watermarks | 每来源/基金成功提交的日期水位 |
| market_revisions | 原值、新值、run_id及修订时间 |
| market_ingestion_logs | 成功/失败与新增、更新数量 |
| tax_policy_versions | 地域、生效/失效日期、不可变版本、来源、审核结构化规则 |
| tax_policy_source_observations | 官方网页/PDF hash变化记录；不会自动激活新规则 |

先完整抓取并验证分页后提交。增量从水位向前重叠7天，捕捉近期修订；重跑相同hash不新增或修订。事务失败回滚，失败日志另存，水位保留。长历史修订应显式指定起始日重新采集；并发采集与商业接口不在当前验收范围。

MySQL用on_duplicate_key_update，SQLite用on_conflict_do_update；默认SQLite实际验证，MySQL尚待真实服务，见mysql.md。

Windows任务计划程序可配置：程序=项目.venv/Scripts/python.exe；参数=`-m finance_agents.data_jobs.scheduler market --instruments 510300`；起始于=项目绝对目录。建议工作日晚间公开净值发布后执行，policy --check-live可每日检查。提供的是一次性CLI，持续调度由操作系统负责，本项目未自动注册你的计划任务。

政策来源变化只标changed_requires_review；人工核验后用--reviewed-policy-file导入新版本和生效日期。不能因正文hash变化直接改税率。来源无数据、重定向、超时或缺页时返回失败而非造数；可查看data-source-validation.md。

已附真实日频510300快照（2026-09-14至2026-10-13，16条）。它不能称为实时交易价格，过期时工具返回不可用，应重新采集。
