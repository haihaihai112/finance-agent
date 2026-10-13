# 数据来源与验证记录

本次核验日期：2026-10-13。市场样本为公开日频单位净值，非交易所实时成交报价；没有虚构行情、税额数据或收益承诺。

## 市场来源

- 固定请求地址：`https://api.fund.eastmoney.com/f10/lsjz`，参数为六位基金代码、日期区间、分页。
- 展示来源：[510300 净值历史](https://fundf10.eastmoney.com/jjjz_510300.html)。
- 实抓时间：`2026-10-13T11:59:10Z`；16 条，日期 2026-09-14 至 2026-10-13；最新单位净值 `4.3479 CNY`。
- 可复核样本：`src/finance_agents/data_jobs/verified_market_snapshot.json`。`MarketStore.load_verified_snapshot()` 显式导入；工具继续检查最近30天新鲜度。样本过期后不会视为当前行情。
- 2026-10-13 再跑实际 CLI，第一次入库16条，第二次重叠窗口重跑新增0、修订0，水位均为2026-10-13。

请求只使用固定域名，不接受用户URL，不跟随重定向；最多20只基金、每页最多100条、最多100页、每请求最多3次尝试、默认10秒超时。取完整页序列后再事务提交；页数达到上限或抓取失败不推进水位。内容哈希只依据业务值，重复采集时间不同不会制造修订。MySQL与SQLite使用原生upsert、唯一业务键；旧值／新值、采集日志和水位留存。重叠补抓默认7天，以发现近期修订。

公开接口不构成已获商业接口授权，尚无可用性服务保证或商业再分发授权核验。正式部署应核验来源条款、获取合规数据许可；站点字段变化及错误会显式失败。510300作为基金单位净值查询样本，不自动判定适合用户，也不把NAV称为ETF成交价。

指标为已观测单位净值区间变化和最大回撤，按实际日期区间、观察数输出；没有总收益、分红再投资、费用或未来收益推断。策略使用固定风险模板并优先保留流动资金，金额按分守恒；负债利率和持仓穿透风险未建模并随结果提示。

## 官方税务证据

| 来源 | 已核验内容 | URL |
| --- | --- | --- |
| 国家税务总局政策法规库 | 2018修正个人所得税法第六条：年度综合所得收入额减60000元及其他依法允许扣除 | https://fgk.chinatax.gov.cn/zcfgk/c100009/c5193028/content.html |
| 上海市税务局附件 | 综合所得年度七档税率、界值与速算扣除数 | https://shanghai.chinatax.gov.cn/zcfw/zcfgk/grsds/202402/P020240202362406243753.pdf |
| 司法部官方答记者问 | 2018修改决定自2019-01-01施行 | https://www.moj.gov.cn/pub/sfbgw/zcjd/201812/t20181228_390204.html |

三份文档在2026-10-13经Firecrawl实抓并检查原文。法律本体有“自公布日起施行”的历史表述，所以当前综合所得制度的2019-01-01生效时间单独引用修改决定说明，不误把法律历史公布日期当2018修改生效日。引用保留原文、URL、版本、生效日期、采集时间；无法核实发布日期的税率附件不填发布日期。

`PolicyStore.seed_verified_rules()` 仅安装已核验版本。查税年2018或未来税年、境外地域、非居民、经营所得／企业税等范围外请求返回不可用；未硬套居民综合所得规则。年度收入必须是确认后的综合所得“收入额”，不自动将劳务流水当收入额；`annual_deductions` 是60000元基本减除之外的年度扣除合计。计算使用Decimal、七档税率、四舍五入至分，结果为未减预缴税额的年度税额，不能称为应补或应退金额。

政策按生效期及地域先过滤，再挑选来源最新适用版本并排序检索。引用预算采用保守UTF-8字节上界，不宣称等于DeepSeek精确token计数。当前仅结构化／文本检索，没有部署向量索引。

官方固定源监测通过 `--check-live` 比较HTML正文文本或PDF内容哈希，将变化记录为 `changed_requires_review`。网页菜单变化也可能触发人工审核；规则须经人工核验后作为新版本导入，不让抓取文本自动变成计算规则。2026-10-13直接HTTP检查税务总局法律及上海PDF成功；司法部源直接请求返回重定向，已拒绝并记录不可用，既有Firecrawl已核验引用保留。变更监测不自动创建系统定时任务。

## 可运行入口

在项目环境安装后：

```powershell
python -m finance_agents.data_jobs.scheduler market --instruments 510300 --start 2026-09-14 --end 2026-10-13
python -m finance_agents.data_jobs.scheduler policy --check-live
python -m finance_agents.data_jobs.scheduler policy --reviewed-policy-file 'data/reviewed-policy-versions.json'
python -m pytest tests/test_market_tax.py -q
```

未指定 `--database-url` 时，采集入口使用与API相同的 `Settings.from_env().database_dsn`（读取项目 `.env`、`DATABASE_URL`、`FINANCE_DATA_DIR`），默认数据库是 `data/runtime/finance.db`，并创建配置的数据目录。`--database-url` 优先覆盖。MySQL连接可以替换为 `mysql+pymysql://...`；需配置真实服务和凭证。Windows任务计划程序或其他调度器可每天调用market入口；代码导入不启动后台任务。本次SQLite实际读写与重跑验证通过，MySQL方言使用SQLAlchemy原生实现但未连接真实MySQL服务，不能声称MySQL实测通过。

当前数据工作包32项测试通过：重复与修订、事务失败回滚、水位失败、分页上限、URL注入拒绝、税阶及非有限值、政策版本及有效期、来源变更不改规则、无行情不作数字结论、配置金额守恒、真实样本完整串联及预算边界。追加回归覆盖税务补答保留原始问题与采集器/API默认数据库一致性。整体集成测试以根协调最终报告为准。

