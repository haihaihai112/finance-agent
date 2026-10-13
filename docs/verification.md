# 验证记录（2026-10-13）

## 完整安装与测试

在独立、没有system-site-packages的Windows CPython 3.13.9虚拟环境执行 `pip install -e ".[dev]"`。首次发现beautifulsoup4未声明，补入pyproject后重新安装；最终 `pip check` 无损坏依赖。

首版历史完整测试：**112 passed, 1 skipped，24.80秒**。跳过的是显式要求MYSQL_TEST_URL的真实MySQL测试；没有可用数据库时不伪装通过。开发虚拟环境较早一轮108通过、1跳过，37.43秒，带原Starlette/httpx弃用提示；最终干净环境无该提示。时间仅是此次测试运行，不是服务响应性能指标。

Ruff check、format --check和compileall通过。锁文件requirements-lock-windows-py313.txt记录干净环境的实际依赖，仅对应Windows/Python3.13，不是跨平台通用锁。

覆盖：五意图与多意图澄清，真实中文PNG/扫描PDF ONNX，表格行提取、全角数字等价规范化、Decimal/币种/亚分金额拒绝、冲突与用户确认、理赔四类初筛、模拟风控，官方个税税阶与历史净值指标，四服务stdio初始化及调用、伪造token/授权/模型行拒绝，上传与跨用户隔离、授权撤销/过期、请求幂等、版本冲突、等待状态重启恢复、取消、异常和无数据边界。

最后追加验证：确认后上传矛盾新材料会撤销相应确认并保留双方证据，重新追问；旧材料的已确认修正值保留有效。金额1,23或1.2.3不被截断成错误数值，合法千分位与万元格式仍可提取。

等待检查点可跨重启恢复；进程强制中断时正在执行的运行不自动重放外部工具，需要查看运行/事件并新建请求。未实现LangGraph原生interrupt/checkpointer。

## 真实数据与采集

根集成复测 `scheduler market --instruments 510300 --start 2026-09-14 --end 2026-10-13`：真实公开来源入库16条、水位2026-10-13。立即重跑：新增0、修订0、拒绝0。SQLite事务与审计自动测试通过；这不是MySQL真实连接验证。

净值为日频单位净值，不是实时交易报价；历史指标不含分红再投资，不能描述为总收益或未来收益预测。官方税务来源核验、政策hash监测及重定向失败记录见data-source-validation.md。

## 浏览器

Playwright驱动真实Edge，项目虚拟环境、无DeepSeek密钥、stdio MCP模式。五意图、PDF上传提取、未确认阻断、独立授权/撤销、刷新恢复、409冲突停止提交与恢复通过；390px手机无横向溢出，零pageerror。中文贷款/税务摘要实际显示并与服务端结果核对，原始事实默认折叠。

人工查看截图：screenshots/loan-summary.png、screenshots/tax-summary.png。首版测试脚本与日志在本机work/browser-*，本次正式脚本在tests/browser/review-revisions.cjs，测试使用合成材料。临时浏览器服务已停止。

## 未验证与实现限制

- DeepSeek有HTTP适配、结构校验和错误降级，MockTransport验证；未配置真实Key，未作付费请求。
- Docker CLI存在但daemon未运行。compose语法用临时占位密码验证通过；MySQL端到端尚未验证。
- OCR没有代表性金融数据集或准确率结论；复杂表格/手写/含文字层和图片的混合页面需扩展。
- 信贷使用模拟规则；理赔只做保单初步筛查；投资使用透明类别模板。没有真实征信、银行审批或赔付操作。
- MCP每次工具调用创建进程，没有连接池；SQL检索不等于向量RAG；本次仅补请求表增量迁移，不等于通用迁移平台。
- 已建立本地Git仓库，分支review-revisions；排除密钥与运行数据。未发布到GitHub。

2026-10-13追加浏览器验证：默认自动识别不发送intent_hint；贷款进入上传等待，多意图进入澄清且不显示错误业务表单，补答后进入贷款；天气问题直接范围外。手动五类仍可指定，自动识别是入口方式而非新增意图。零pageerror，测试日志work/browser-auto-log.json。


## 审阅修订验收

2026-10-13：独立、无 system-site-packages 的 Python 3.13.9 环境最终完整回归 **163 passed，4 skipped，25.33 秒**，无测试警告。四项跳过是专用真实 MySQL 验收，缺 MYSQL_TEST_URL，不能视为通过。

新增验证覆盖请求登记与运行创建/恢复、最终状态与响应、复制申请写入的事务回滚；请求状态/运行关联/用户隔离；超过5分钟无更新的请求显式关闭、撤销授权和迟到结果拒绝；贷款复制字段/材料/证据、确认和授权重置、父运行关联及旧结果不变。模型与直接API拒绝空白/超长身份业务字段；不定义新增银行就业规则。

旧请求表增量迁移在SQLite覆盖重复执行、空白包围的JSON空值及301条历史记录；按128条分批解码结果，保持原始结果不变；旧未完成请求标为interrupted，无法可靠关联的旧run_id保持空值，不猜测对应运行。迁移前备份数据库和文件，停止写入，让一个进程先完成迁移，再启动多个worker。真实MySQL迁移未实测。

资源测试覆盖锁互斥/取消/回收，OCR上限、同材料任务复用、每次读取授权、返回副本以及缓存/文件访问在线程执行。实际RapidOCR ONNX合成图片小批量测量为4张/8请求，上限与工作线程峰值均为2，实际提取4次；这不是代表性金融票据基准，不报告准确率或生产吞吐目标，见[资源与外部验收](resources-and-external-verification.md)。

真实外部验收入口已扩展：MySQL贷款API和stdio、保单stdio、并发CAS、采集回滚4项；当前4 skipped。DeepSeek脚本最多两次合成文本请求，记录耗时及token usage；当前无Key，实际调用0次。受控超时和网络错误降级通过，不替代真实模型验证；未核验单价时费用为空。

正式浏览器脚本已在真实Edge/独立SQLite/规则模式/stdio MCP运行一次：四种理赔显示夹具通过，实际贷款完成→刷新材料→复制→金额12000变24000、期限12变24→重新确认授权→再评估通过，旧结果不变，零pageerror。理赔夹具仅验证显示，条款逻辑另有Python测试。最终原子复制与资料恢复故障修正后，真实Edge/隔离Python/stdio再次执行BROWSER_CASE=all：四种显示夹具、元信息及提取失败两个恢复夹具、实际贷款复制与新授权全部通过，零pageerror。复制后字段确认清空，旧结果和旧授权记录保留。

本地Git及scripts/verify.ps1使代码验收可重复；scripts/package_source.py只打包源代码和交付说明，排除密钥、运行数据、虚拟环境、Git元数据和中间验收材料。


最终执行scripts/verify.ps1（隔离Python）：163 passed、4 skipped；Ruff check通过；format --check报告76 files already formatted；compileall通过。新增DeepSeek验收脚本的模型匹配/缺失/不一致、无Key及异常usage等测试均为合成transport，不构成真实调用证据。正式浏览器全场景通过，服务已停止。源码包和本地Git整理结果见最终交付。
