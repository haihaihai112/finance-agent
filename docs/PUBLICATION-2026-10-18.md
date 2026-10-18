# 2026-10-18 发布副本复核 / Publication-copy verification

本记录描述推送前已完成的副本核验，不单独证明 GitHub 发布完成。副本从远程 `main` 的 `9e81a6ac4ff6819c8ed70561be3ad952d2894b36` 创建，导入原项目 `f6921eac372acd72c1365df4981133aa0c3a0be3` 已跟踪业务文件。原项目只读；未移植其提交历史。远程双语 README、来源与许可说明、历史发布说明和额外忽略规则保留；未新增项目许可证。

This records completed checks before pushing; it does not by itself establish publication. The copy starts from remote `main` at the SHA above and imports tracked business files from the source SHA without replacing remote history. Bilingual README structure, attribution/license notices, historical publication notes and additional ignore rules are retained. No project license is added.

## 本轮实际运行 / Checks performed

| 检查 / Check | 结果 / Result |
| --- | --- |
| Environment | Windows, PowerShell 7; reused source `.venv`, Anaconda Python 3.13.9, `include-system-site-packages=true`; **not a clean install** |
| Imported application | CWD and `PYTHONPATH` target the publication copy; `finance_agents.__file__` confirmed its `src` directory |
| External-call isolation | `LLM_ENABLED=false`, empty model key, no `MYSQL_TEST_URL` or `DATABASE_URL`, no copied `.env`; temporary ignored `sitecustomize` blocks external socket connections, including child Python processes |
| `python -m pytest -q -rA` | **219 passed, 4 skipped, 1 warning in 65.84 seconds**; four skips require a dedicated real MySQL database; one Starlette/TestClient `httpx` deprecation warning |
| OCR and MCP | The full suite includes actual local ONNX inference on synthetic Chinese image/scanned PDF, plus local `stdio` MCP tests; model files were already available |
| `python -m ruff check src tests scripts` | Passed |
| `python -m ruff format --check src tests scripts` | 84 files already formatted |
| `python -m compileall -q src scripts` | Exit 0 |
| `node --test tests/browser/workflow-view.test.cjs` | 5 passed, 0 failed; these are state-description unit tests, not browser rendering tests |
| `scripts/doctor.py --json --port 8873` | Exit 0; default port 8000 was occupied and correctly blocked the first diagnostic run |
| Screenshot inspection | Viewed source-HEAD desktop/mobile homepage JPEGs; they show the empty interface without personal documents. This is inspection of existing evidence, not a new browser run |

测试耗时仅描述本轮测试，不代表接口延迟或生产容量。临时 harness 和测试产物位于被忽略的 `work/`，不进入公开源码。VS Code 任务的 shell 命令改为 `pwsh`，遵守 PowerShell 7 使用约定。

Test duration is not an API latency or capacity measure. The temporary harness and test output stay under ignored `work/`. VS Code tasks now explicitly use `pwsh` in accordance with the PowerShell 7 requirement.

## 历史证据与本轮未运行 / Historical evidence and excluded checks

- 原项目 2026-10-18 的 DeepSeek 真实调用记录包含结构化生成、应用自动路由与税务结果整理，使用合成文本。详见 [DeepSeek 记录](deepseek-live-verification-2026-10-18.md)。本轮没有调用付费 API，也未把历史调用当成本轮重跑。
- 本轮没有启动真实 MySQL、采集新行情、访问政策网站、执行完整浏览器交互或重装依赖。历史 UI 证据见 [界面验收](minimal-ui-verification-2026-10-18.md)；业务修订与复测见 [体验报告修订](report-revisions-2026-10-18.md)。
- No paid model requests, real MySQL acceptance, fresh market/policy fetching, full browser interactions or dependency reinstall were performed in this publication check. Historical evidence retains its original date and conditions.
- 保单演示与风控仍是明确标识的模拟；没有新增真实授信、赔付、代表性 OCR 准确率、生产吞吐或业务收益结论。Private configuration, runtime databases, uploaded documents, caches and conversational handoff files are excluded.
