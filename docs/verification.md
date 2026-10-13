# 验证记录（2026-10-13）

## 完整安装与测试

在独立、没有system-site-packages的Windows CPython 3.13.9虚拟环境执行 `pip install -e ".[dev]"`。首次发现beautifulsoup4未声明，补入pyproject后重新安装；最终 `pip check` 无损坏依赖。

最终完整测试：**112 passed, 1 skipped，24.80秒**。跳过的是显式要求MYSQL_TEST_URL的真实MySQL测试；没有可用数据库时不伪装通过。开发虚拟环境较早一轮108通过、1跳过，37.43秒，带原Starlette/httpx弃用提示；最终干净环境无该提示。时间仅是此次测试运行，不是服务响应性能指标。

Ruff check、format --check和compileall通过。锁文件requirements-lock-windows-py313.txt记录干净环境的实际依赖，仅对应Windows/Python3.13，不是跨平台通用锁。

覆盖：五意图与多意图澄清，真实中文PNG/扫描PDF ONNX，表格行提取、全角数字等价规范化、Decimal/币种/亚分金额拒绝、冲突与用户确认、理赔四类初筛、模拟风控，官方个税税阶与历史净值指标，四服务stdio初始化及调用、伪造token/授权/模型行拒绝，上传与跨用户隔离、授权撤销/过期、请求幂等、版本冲突、等待状态重启恢复、取消、异常和无数据边界。

最后追加验证：确认后上传矛盾新材料会撤销相应确认并保留双方证据，重新追问；旧材料的已确认修正值保留有效。金额1,23或1.2.3不被截断成错误数值，合法千分位与万元格式仍可提取。

等待检查点可跨重启恢复；进程强制中断时正在执行的运行不自动重放外部工具，需要查看运行/事件并新建请求。未实现LangGraph原生interrupt/checkpointer。

## 真实数据与采集

根集成复测 `scheduler market --instruments 510300 --start 2026-09-14 --end 2026-10-13`：真实公开来源入库16条、水位2026-10-13。立即重跑：新增0、修订0、拒绝0。SQLite事务与审计自动测试通过；这不是MySQL真实连接验证。

净值为日频单位净值，不是实时交易报价；历史指标不含分红再投资，不能描述为总收益或未来收益预测。官方税务来源核验、政策hash监测及重定向失败记录见data-source-validation.md。

## 浏览器

Playwright驱动真实Edge，项目虚拟环境、无DeepSeek密钥、stdio MCP模式。五意图、PDF上传提取、未确认阻断、独立授权/撤销、刷新恢复、409冲突停止提交与恢复通过；390px手机无横向溢出，零pageerror。中文贷款/税务摘要实际显示并与服务端结果核对，原始事实默认折叠。

人工查看截图：screenshots/loan-summary.png、screenshots/tax-summary.png。原测试脚本与日志在本机work/browser-*，测试使用合成材料。临时浏览器服务已停止。

## 未验证与实现限制

- DeepSeek有HTTP适配、结构校验和错误降级，MockTransport验证；未配置真实Key，未作付费请求。
- Docker CLI存在但daemon未运行。compose语法用临时占位密码验证通过；MySQL端到端尚未验证。
- OCR没有代表性金融数据集或准确率结论；复杂表格/手写/含文字层和图片的混合页面需扩展。
- 信贷使用模拟规则；理赔只做保单初步筛查；投资使用透明类别模板。没有真实征信、银行审批或赔付操作。
- MCP每次工具调用创建进程，没有连接池；SQL检索不等于向量RAG；建表不等于数据库迁移。
- 当前没有Git仓库或发布到GitHub。源码、说明和演示边界均为本机个人项目交付。

2026-10-13追加浏览器验证：默认自动识别不发送intent_hint；贷款进入上传等待，多意图进入澄清且不显示错误业务表单，补答后进入贷款；天气问题直接范围外。手动五类仍可指定，自动识别是入口方式而非新增意图。零pageerror，测试日志work/browser-auto-log.json。
