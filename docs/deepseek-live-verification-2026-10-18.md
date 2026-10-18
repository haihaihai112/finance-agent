# DeepSeek 本机真实调用验收（2026-10-18）🔧

本记录更新此前“DeepSeek尚未配置”的状态。用户在本地 .env 填写密钥后，本机完成真实 API 及运行中 HTTP 服务验证；没有上传真实身份信息或金融单据，全部模型测试使用合成文本。

## 配置与直接调用证据

- 本地 .env 已被 Git 忽略，密钥未写入报告。官方接口为 https://api.deepseek.com ，启用 LLM，模型为 deepseek-flash。
- 真实 GET /models 返回200，包含 deepseek-flash、deepseek-v4-pro；当前使用 deepseek-flash。
- 实际项目 DeepSeekClient + IntentRouterAgent 识别合成贷款文本，source=deepseek、intent=loan、无需意图澄清。
- scripts/verify_deepseek.py 修复后实际调用两次，均HTTP200，模型身份与配置相符，JSON经IntentDecision验证；贷款返回loan，税务返回policy_tax。
- 这两次请求分别记录 prompt/completion/total tokens 为362/145/507、357/182/539；耗时约1.4031及1.3244秒。这只是两条样例，不是全部调试消耗、稳定延迟或业务性能指标。未核验单价，未估算人民币费用。
- 受控超时／网络错误降级检查通过，属于合成故障测试，不是真实网络故障复现。

## 实际发现并处理的问题

1. 原验收脚本max_tokens=128，实际响应包含reasoning_content，finish_reason=length，JSON被截断。保留诊断字段长度与用量，不记录推理正文。脚本改为与应用相同的2048有限额度；新增受控截断回归用例先失败，修复后通过，真实两次请求随后通过。应用原本已经使用2048额度。
2. 首次应用内税务测试识别了policy_tax，却因缺少收入／扣除资料设置requires_clarification，进入意图澄清而非税务表单。SQL事件与HTTP响应可定位该现象。收紧路由提示词：分类置信度与资料完整性分开，业务明确时由专业Agent收集资料。重启后完全相同的HTTP测试通过。提示词改进不构成代表性路由准确率保证，后续仍需人工多样化测试。

第一次失败的应用运行ID为9263e4e80119414ba186ac0676cb7fbb，保留在本轮数据库中；没有删除失败历史。

## 运行中服务的验收

入口 http://127.0.0.1:8000 ，沿用data/local-test-20261018-1437独立测试数据目录及原日志配置。服务已重启加载密钥，health中model_configured=true、mcp_transport=stdio、credit_mode=mock。external_services_verified仍为false：该字段是当前代码的总体标记，不能据此推断本次DeepSeek调用失败，也不能改成true来掩盖MySQL及真实金融服务未验证。

合成税务HTTP验收成功运行ID：565b0da493994fe4b42b3dc938f1e7bb。

实际流程：不发送intent_hint → DeepSeek自动分类 → 等待税务字段 → HTTP提交确认的全年收入120000、额外年度扣除0、2026年中国大陆居民综合所得 → stdio税务MCP计算 → 模型整理 → completed → JSON/Markdown导出。

事件记录mode=deepseek、路由警告0；年度税额3480.00，3条引用，无模型整理降级警告。该税额未减预缴税额，不等于补退税。模型不代替确定性计算。

## 本地证据位置

本次在独立、无system-site-packages的Python3.13环境运行scripts/verify.ps1：**182 passed、4 skipped，39.56秒**。四项跳过仍为需专用测试库的真实MySQL测试；Ruff check、80文件格式检查和compileall通过。相较历史181项，新增一项验收额度截断回归。没有把受控测试当作真实API调用。

本机项目根目录下的data/local-test-20261018-1437/：

| 文件 | 内容 |
|---|---|
| deepseek-preflight.json | 密钥存在布尔值、模型列表及HTTP状态，无密钥 |
| deepseek-response-diagnosis.json | 截断结束原因、内容长度、token用量，无推理正文 |
| deepseek-adapter-live.json | 实际适配器贷款分类结果 |
| deepseek-live-verification.json | 修复后两次直接API结构化验证及用量 |
| deepseek-app-test.py | 应用内有限HTTP验收脚本 |
| deepseek-app-verification.json | 成功税务流程、模型路由来源、计算及导出结果 |
| deepseek-code-verification.log | 本次代码回归日志，以实际完成输出为准 |
| access.log、server.log、finance.db | 请求、服务状态和业务事件 |

敏感配置、运行数据库和上传材料不进入源码包。密钥存在、health标记为true均不能单独替代上面的实际调用证据。

## 尚待人工验收

测试者可刷新工作台并选择“自动识别”，输入单一业务；手动指定业务会绕过模型分类，不宜作为真实路由验证。继续测试缺失／矛盾资料、真实脱敏材料、授权撤销和异常恢复，按feedback.md记录时间、步骤和运行ID。

本次仅证明deepseek-flash在此账号、本机、合成样例上可调用，并完成了一条真实模型参与的税务业务链路。贷款／保单仍为mock，真实风控、MySQL和生产部署没有因此通过验收。整体落地目标仍待用户测试报告与后续迭代。
