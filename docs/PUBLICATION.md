# 发布检查 / Publication checks

Date: 2026-10-14 (Asia/Shanghai)

| 检查 / Check | 结果 / Result |
| --- | --- |
| Clean installation | Independent Python 3.13.9 virtual environment; requirements-lock-windows-py313.txt + editable package |
| Existing tests | 181 passed, 4 skipped in 47.29 seconds |
| Skips | Four opt-in real MySQL tests; dedicated database not configured |
| Ruff | src and tests passed |
| Offline doctor | Exit 0; SQLite configuration, stdio MCP and local ONNX files recognized |
| Windows startup | PowerShell 7.6.5: scripts/start.ps1 launched; /, /docs and /openapi.json returned HTTP 200 |
| External calls | No paid model calls or external database verification |

本次保留此前 Git 提交历史。历史文档中“未发布到 GitHub”等表述描述的是 2026-10-13 的交付状态；当前发布状态以仓库和本记录为准。当前规则模式及模拟金融业务边界保持不变。

The existing Git history is preserved. Statements such as "not published to GitHub" in historical documents describe the October 13 delivery state. This repository and this note describe the publication state. Rule-mode and simulated financial workflows are unchanged.

启动检查发现 Windows PowerShell 5.1 将无 BOM 的中文 UTF-8 脚本按系统编码读取，导致解析失败。发布副本为 scripts/start.ps1 与 scripts/verify.ps1 添加 UTF-8 BOM，脚本文本和业务逻辑未修改。使用 Windows PowerShell 5.1 重新启动验证通过。

Windows PowerShell 5.1 misread the Chinese UTF-8 startup script without a BOM and failed to parse it. The publication copy adds a UTF-8 BOM to scripts/start.ps1 and scripts/verify.ps1, preserving the decoded script text and business logic. Startup was then verified. The final publication startup check used PowerShell 7.6.5; PowerShell 7 (pwsh) is the recommended shell.

后续命令与脚本调用统一使用 PowerShell 7（pwsh）；最终启动复核已在 PowerShell 7.6.5 完成。

MySQL 联调和 DeepSeek 真实 API 未在本次验证中执行。Test results do not establish financial accuracy, representative OCR accuracy, production capacity or business returns. No new project license was assigned.
