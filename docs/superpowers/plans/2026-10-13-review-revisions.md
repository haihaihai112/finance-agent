# Review Revisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 修复审阅中已核验的业务闭环、事务可靠性、表单校验和资源问题，并使验收可重复。

**Architecture:** 使用现有 FastAPI/SQLAlchemy 工作流，显式复制贷款运行，不改变历史记录。请求与运行采用事务和版本控制；外部调用中断不自动重放。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy、LangGraph、MCP、RapidOCR、原生 JavaScript、pytest、Playwright。

**Spec:** `docs/review-revision-spec.md`

## Global Constraints

- 保持五意图、六 Agent、四 MCP。
- 金融金额保持 Decimal/字符串；OCR 不越过确认。
- 贷款规则与保单为 mock；中国大陆/CNY/基金日频净值/居民年度综合所得个税。
- 不创建或发布 GitHub 仓库。
- 未连接真实服务时明确未验证，不能以 mock 代替。

### Task 1: 请求事务和贷款复制闭环

**Files:** `storage/runtime_store.py`、新增 `storage/runtime_migrations.py`、`workflow/service.py`、`workflow/state.py`、`contracts/requests.py`、`contracts/results.py`、`api/routes.py`、`agents/credit_assessment.py`、新增 `tests/test_request_recovery.py`、`tests/test_loan_copy.py`。

**Interfaces:** POST `/api/v1/runs/{run_id}/copy` 接收 `{request_id}`；返回 WorkflowResponse，包含 `parent_run_id` 和 `document_ids`。GET `/api/v1/requests/{request_id}` 返回当前用户请求状态/运行关联/时间；POST 同路径 `/interrupt` 仅关闭超过 5 分钟无更新的 processing 请求，不重放工具。

- [ ] 增加故障测试：登记占位后创建失败必须全部回滚；最终响应保存失败必须回滚运行版本；API 请求查询隔离用户。
- [ ] 增加贷款复制测试：已完成贷款复制后的字段值与证据保留，全部确认/授权清空，父运行不可变，旧授权不能授权新运行；复制请求幂等。

```python
assert copied.parent_run_id == old.run_id
assert copied.credit_form.confirmed_fields == []
assert copied.consent_ref is None
assert store.get_run(old.run_id, old.user_id).final_response == old.final_response
```

- [ ] 执行 `.venv/Scripts/python.exe -m pytest tests/test_request_recovery.py tests/test_loan_copy.py -q`，先证明测试失败。
- [ ] 实现事务方法/实际旧表迁移/中断终结 CAS 与复制 API；同步持久化放到 `asyncio.to_thread` 或同步 API 线程处理。复制直接等待确认，不执行外部工具。
- [ ] 执行以上新测试及现有 API/storage/MCP 测试，报告实际结果。只提交本任务负责文件。

### Task 2: 前端理赔契约、复制交互与语义校验

**Files:** `web/app.js`、`web/index.html`、`web/styles.css`、`contracts/profiles.py`、新增 `tests/test_form_semantics.py`、`tests/browser/review-revisions.cjs`、浏览器验收说明。

**Interfaces:** 消费 Task 1 复制 API 及 WorkflowResponse 的 `parent_run_id`/`document_ids`。复制后全部确认框为空，须新授权；未知字段不自动提交。理赔覆盖 `potentially_covered/outside_coverage_dates/excluded_by_provided_clause/requires_manual_clause_review`。

- [ ] 增加模型与直调 API 负向测试，覆盖 `subject_ref/employment_status/policy_id/policy_version` 空白与过长。

```python
with pytest.raises(ValidationError):
    CreditForm(subject_ref="   ")
```

- [ ] 执行 `.venv/Scripts/python.exe -m pytest tests/test_form_semantics.py -q`，先证明失败。
- [ ] 添加有边界的非空字符串契约，无新增银行就业枚举；补四种中文摘要并核对 `within_effective_dates`；实现明确“复制为新申请”按钮和历史关联显示。
- [ ] 浏览器脚本通过环境变量配置 Playwright 路径/浏览器/服务 URL；四种显示使用受控响应夹具，贷款复制再确认授权走真实 API。用真实 Edge 执行并报告。
- [ ] 执行表单/文档/API 回归测试，提交本任务文件。

### Task 3: OCR 与锁资源界限、外部服务验收

**Files:** 新增 `workflow/locks.py`、`tools/document_service.py`、`core/settings.py`、新增 `tests/test_resources.py`、扩展 `tests/test_mysql_integration.py`、新增 `scripts/verify_deepseek.py`、新增 `scripts/measure_concurrency.py`、`.env.example`、外部验证/并发文档。

**Interfaces:** `KeyedLocks.hold(key)` 异步上下文，退出后无持有者/等待者则释放字典条目，Task 1 将其接入 WorkflowService；DocumentService 支持配置 `max_concurrency`，只允许有限 OCR 工作，不引入基础设施。

- [ ] 测试同键互斥、不同键独立、等待取消后回收；OCR 同时工作数量不超过配置，同材料并发复用缓存，不暴露跨用户结果。

```python
assert peak_ocr <= configured_limit
assert len(locks) == 0
```

- [ ] 执行 `.venv/Scripts/python.exe -m pytest tests/test_resources.py -q`，先证明失败。
- [ ] 实现锁/OCR 资源界限，缓存读写/文件读在线程中执行，添加有限并发测量输出。
- [ ] 真 MySQL 用显式专用测试 URL，覆盖真实 API、stdio MCP、竞争 CAS 和故障注入采集事务回滚。未配置则 skip 并记录。
- [ ] DeepSeek 脚本最多两次合成请求，不泄漏 key/敏感异常，记录模型可用性、耗时与 usage；费用无单价时不编造。网络 timeout/error 降级分别记录为受控验收。
- [ ] 运行可用验收，报告实际外部服务配置存在与否、实际通过/跳过数量，只提交本任务文件。

### Task 4: 集成审阅、发布整理

**Files:** `.gitignore`、`README.md`、`docs/verification.md`、`docs/project-highlights.md`、源码 zip。

- [ ] 在本地 revision 分支建立基线 Git，忽略 `.superpowers/`、浏览器报告等中间材料，不改全局 Git 配置。
- [ ] 审阅三项任务接口，执行完整 pytest/ruff/compileall 和正式浏览器脚本。
- [ ] 独立审阅实际 diff，修复有证据的阻断问题，执行相关复验。
- [ ] 文档记录修订与剩余外部验证限制；更新源码 zip，确认不含 `.env`/运行数据/虚拟环境/`.git`。
