# 资源控制与外部服务验收

2026-10-13 本机结果：资源回归 37 passed；真实 MySQL 4 skipped；DeepSeek 未配置密钥，实际网络调用 0 次。受控超时与网络错误规则降级分别通过。未连接真实服务的项目仍为未验证，不能以 SQLite 或受控 transport 测试替代。

## 锁与 OCR

`KeyedLocks.hold(key)` 引用计数包含持有者和等待者。正常退出、业务异常、取消持有者或取消等待者都会减少引用；归零即回收条目。同键互斥，不同键独立。这个锁属于当前事件循环和进程；多进程的数据冲突仍由数据库 CAS 处理。

`OCR_MAX_CONCURRENCY=2` 默认限制每个 DocumentService 的工作数量。等待请求和已取消请求共享同用户、同 document_id 的提取任务；每个调用者都先授权。缓存、文件读写及数据库访问通过线程执行。返回提取对象为独立副本；同字节的不同上传仍是不同材料，不跨用户共享结果。

取消调用者不能停止底层 OCR 线程，因此实际工作会继续直到完成，并保留并发槽位。成功后保存缓存；失败后释放条目，后续显式请求可以重试。没有自动重放金融工具。缓存只存证据，所有财务字段仍需用户确认。

## 有限并发测量

```powershell
.venv/Scripts/python.exe scripts/measure_concurrency.py --mode image --documents 4 --duplicates 2 --limit 2 --output work/concurrency-acceptance.json
.venv/Scripts/python.exe -m pytest tests/test_resources.py tests/test_documents_credit.py -q
```

脚本只生成合成材料，运行实际 DocumentService、DocumentStore、SQLite 文件库、RapidOCR ONNX；不是使用 sleep 模拟吞吐。`--mode text-pdf` 可测实际 PyMuPDF 原生文本解析路径，不能把该结果称为扫描件 OCR 性能。参数最多 16 份材料、每份 4 次请求、上限 8；无无限压测。

本机 Windows / Python 3.13.9 一次 image 模式测量：

| 指标 | 实际观察 |
|---|---:|
| 合成图片数 / 请求数 | 4 / 8 |
| 配置上限 / 工作线程峰值 | 2 / 2 |
| 实际提取次数 | 4 |
| 独立预热时间 | 3.3672 秒 |
| 首次整批时间 | 7.8591 秒 |
| 4 份材料缓存整批时间 | 0.0054 秒 |
| 8 个重复请求的观察速率 | 1.02 次/秒 |

这是固定合成图片、预热模型、小批量本机测量；结果随机器、图片、并发和缓存变化，不代表真实业务负载、生产性能目标或 OCR 准确率。8 次请求中有 4 次合并复用，速率不等于每秒处理独立图片数。`work/` 的 JSON 为忽略的中间验收输出。

## MySQL 真实集成

只读取显式 `MYSQL_TEST_URL`，必须为带数据库名的 MySQL URL，不回退 `DATABASE_URL`，不自动启动或安装数据库。详见 [MySQL 配置](mysql.md)。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_mysql_integration.py -q
```

四项入口覆盖：贷款 HTTP API 的创建、材料提取、字段确认、授权、实际 stdio MCP 子进程评估及跨用户隔离；独立 claims stdio 读取共享 DB；两个线程在同版本竞争 CAS（一个提交、一个冲突）；采集在第二次水位写入前故障注入，验证记录、修订、水位及成功日志整批回滚，失败日志独立保留。贷款评估、保单仍明确为 mock，MySQL 协议和事务验收不改变金融规则标签。

测试创建合成会话、演示保单和独立来源行情记录，保留记录，不执行删库、删表或清理业务数据。测试 URL 的专用性由配置者保证。2026-10-13 没有配置该 URL，4 项真实验收均 skipped；未获得真实 MySQL 的通过证据。本地 SQLite 对同一验收主体的 4 项自检仅验证脚本流程与断言，不计入 MySQL 验收结果。

## DeepSeek 真实及受控验收

```powershell
.venv/Scripts/python.exe scripts/verify_deepseek.py --calls 2 --timeout 15 --output work/deepseek-acceptance.json
```

从本地环境或 `.env` 读取密钥。最多两次、无重试，只发预定义合成意图文本和 JSON Schema，不发送材料、姓名、金额、身份或业务记录。输出仅包含密钥存在性、请求状态、耗时、经过白名单筛选的 token usage、模型响应是否匹配配置与结构化验证结果；不输出密钥、URL、原始异常、provider 错误正文或完整模型回答。

`controlled_fallbacks.timeout` 与 `network_error` 使用合成 transport 验证实际 DeepSeekClient + IntentRouterAgent 的规则降级，分别标为 controlled_test。它们不证明真实网络、模型支持或可用性。

缺少密钥时 `live_status=unverified_missing_key`、`live_requests=[]`、`model_available=null`。配置的模型名称不能视为实际支持证据；只有实际请求获得有效结构化响应才记为可用。实际 HTTP、超时、网络及结构化响应错误分别记录，不透露错误正文。没有经过核验的单价时 `cost=null`，不根据 token 数猜测费用。本次密钥不存在，实际模型、耗时、usage 和费用均未验证。

依赖警告：当前 FastAPI/Starlette TestClient 的 httpx 兼容弃用警告和 PyMuPDF 的 fitz 名称弃用警告均来自已安装依赖；不将警告称为业务测试失败。
