# 完整个人项目实施计划（已授权）

依据：architecture.md、review-checklist.md 与用户 2026-10-13 的实现授权。
目标：填充现有 Python 项目，五类意图／六 Agent／四 MCP，提供可操作的完整业务流程。
技术：FastAPI、Pydantic、LangGraph、SQLAlchemy、MySQL／本地 SQLite、OCR 适配器、MCP SDK。

## 约束

- 专业结果经优化输出；其他意图直接范围外提示；不新增 Agent。
- 金额用 Decimal；资料缺失追问，OCR 字段确认后才能进入计算。
- 征信、风控及保险演示样本必须显式标为模拟或脱敏；政策／行情有真实来源和时间。
- 外部不可用时返回不可用，不将模拟价格／税法冒充真实。
- 核心流程无需付费模型密钥可验证，模型适配器可配置；此模式不能声称 LLM 已接入。
- 使用可替换的业务仓储；默认本地 SQLite，提供 MySQL 环境与迁移路径及测试。
- 当前目录不是 Git 仓库；原路径直接实现以保留用户 VS Code 工作区，不发布外部仓库。

## 接口冻结

SessionState 延用骨架，新增 message、last_question、final_response。Agent.run(state) 返回 AgentResult。
needs_input 的 missing_fields 表达待补字段；confirmed_fields、document_refs、consent_ref 为确认、上传和授权节点。
四个 MCP call(tool_name, arguments, auth) 返回 ToolResult，失败可分类。严格保留 data_mode 与引用。
文档服务由 DocumentStore(engine, storage_dir) 管理；按用户与文档 ID 校验所属关系。
ClaimIntakeAgent(mcp, document_service)／CreditAssessmentAgent(mcp, document_service) 使用共享工具。
AssetAllocationAgent(mcp)／TaxPolicyAgent(mcp) 使用已确认画像。
root 集成时可根据测试结果调整构造器，并记录改动。

## 工作包与依赖

1. 根协调：配置、数据库引擎、会话／鉴权／授权／幂等、LangGraph、API、输出优化。
2. 文档工作包：上传存储、PDF／图片 OCR、证据字段、信用表单、理赔／风控 MCP 与 Agent；独占相应文件。
3. 数据工作包：真实来源适配、MySQL 幂等采集、水位与修订、政策检索／规则、行情／财税 MCP 与 Agent；独占相应文件。
4. 集成：所有工作包完成后加入实际 MCP 传输、四分支 API 测试、恢复／越权／无数据测试。
5. 交付：更新 README、亮点、演示样本、启动脚本、MySQL 配置与验证报告。

## 验收

- 明确投资、理赔、贷款、政策／税务、其他与意图追问；无凭据补全用户资产。
- 资产字段不全先追问；配置金额和比例守恒；行情不可用不返回编造指标。
- 上传文件可解析，OCR 关键字段有来源且未经确认不能进入理赔／风控。
- 信用表单支持提示预填、冲突追问、确认与授权，确定性模拟风控可复算。
- 授权拒绝／过期不查询；跨用户文档、运行、历史保单访问被拒绝。
- 政策有效期／地域过滤与确定性税额，真实引用可打开。
- MySQL／SQLite 幂等入库、重跑、水位、修订记录与工具故障处理可测。
- LangGraph 可运行，等待／恢复与版本冲突可测；实际 MCP 客户端集成测试。
- fresh install、API 文档、脱敏演示与全套测试通过；无业务 NotImplemented 残留。

最终业务实现的真实性边界：真实日频NAV与官方税务资料、模拟保单与征信规则；MySQL测试仅有可选入口且当前未配置。等待状态SQL恢复已完成，LangGraph原生检查点/向量RAG/实时交易行情不在首版实现中。
