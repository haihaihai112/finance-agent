# 个人项目交付设计与实施计划

日期：2026-10-13。基线：4855340。用户授权自主完成个人项目，明日验收。

## 目标与验收

在已有金融工作流上补齐可以启动、操作、恢复、解释、导出和验收的完整个人项目体验。保留已有业务范围，不增加新的金融产品与审批规则。

## Global Constraints

- 中文工作台；五意图、六 Agent、四 MCP；澄清为状态。
- 中国大陆、CNY、基金/ETF日频公开净值、居民年度综合所得个税；不称实时行情。
- 金额使用Decimal与字符串，不经JavaScript浮点转换；OCR字段必须核对确认。
- 信贷与演示保单明确mock；真实MySQL和DeepSeek未配置就明确未验证。
- 所有业务读取、事件与导出必须验证当前用户归属；不输出Token、授权引用、密钥。
- 请求定位只读取，不自动重放工具；新的申请/继续/复制必须保存request_id，恢复必须显式操作。
- 保留现有幂等、版本冲突、授权撤销、材料冲突和人工中断语义。
- 本地交付，不发布GitHub；测试与打包排除.env和运行资料。

## 接口设计

- GET /api/v1/runs 保持WorkflowResponse数组返回，新增limit(1..100，默认50)、offset(>=0)；按创建顺序最新在前，SQLite/MySQL兼容。
- GET /api/v1/runs/{id}/events 使用已有鉴权接口。前端只展示事件中文类型和UTC时间，避免直接输出内部payload。
- GET /api/v1/runs/{id}/export?format=json|markdown 返回附件下载；使用白名单构造报告，包含run_id、parent_run_id、intent、status、state_version、result与data_scope；仅有result时可导出，无result返回409。结果包括事实、引用、限制，不能导出内部状态、表单、凭据与授权。导出不触发模型或MCP。
- 前端api下载支持Bearer；下载路径不把Token拼入URL。
- sessionStorage保存待定位请求元数据(request_id、operation)，不保存message、表单、材料；session切换清除。
- GET requests用于定位run并读取；processing只显示状态及显式刷新，超5分钟可显式中断；中断后读取run，无自动重试。

## Task 1: 历史列表与结果报告后端

Ownership：api/routes.py、storage/runtime_store.py、新 reporting.py、tests/test_delivery_api.py。

先写并运行失败测试，再实现列表稳定排序分页、报告JSON/Markdown下载。测试精确金额、来源与模拟边界保留、鉴权与跨用户、非法参数、无结果409、只读不执行外部工具、不输出授权引用。普通def读路由保持FastAPI线程池语义。独立报告记录RED/GREEN、命令与结果；只提交自己文件。

## Task 2: 工作台交付体验

Ownership：web/app.js、web/index.html、web/styles.css、tests/browser/personal-delivery.cjs。

历史列表带刷新、上一页/下一页、可选读取；读取材料成功后再切运行，读取失败保持现有编辑。运行事件类型与时间中文展示。JSON/Markdown显式下载。保存mutation请求ID，网络中断/响应丢失后定位请求，不自动再次POST；刷新页面保存恢复入口；区分请求completed与业务waiting/completed。保留conflict和授权行为。

先建立可失败的浏览器验收，后实现。以Edge验证桌面和390px，包括跨历史切换、事件、下载精度、首次POST响应丢失后的请求定位、刷新页面恢复、未知请求和409中断反馈。报告实际命令证据；只提交自己文件。

## Task 3: 启动诊断与验收手册

Ownership：scripts/doctor.py、scripts/start.ps1、.vscode/tasks.json、tests/test_doctor.py、docs/personal-project-acceptance.md、docs/workbench-guide.md、README.md、docs/project-highlights.md。

诊断以只读离线检查为主：Python、核心包、.env存在性、配置模式、OCR模型本地可用性、端口是否已使用；不打印任何凭据，不下载模型，不写数据库，不默认探测外部服务。阻断/未配置/可选分别说明，缺依赖有非零退出码；可JSON输出。启动脚本检查错误退出，给出访问地址，支持-Port。VS Code配置启动/诊断/代码检查任务。

写清Windows安装、启动、五意图验收样例、材料确认与模拟授权、历史/事件/导出、请求异常恢复、已知限制和外部验证步骤；连接未配置不得宣称完整真服务。保留面试亮点并区分已实现/未验证。适当行为测试RED/GREEN；只提交自己文件。

## Task 4: 整体验收与交付

独立审阅各任务与整轮diff，修复重要问题。干净venv跑verify.ps1；隔离SQLite与stdio启动服务，运行两套正式Edge浏览器验收；记录真实结果与skip。更新docs/verification.md、重新安全打包并校验、Git本地提交；具体工作完成后暂停夜间heartbeat，不再扩需求。
