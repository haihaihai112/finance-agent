# MySQL本地演示

默认SQLite可直接运行。MySQL配置使用SQLAlchemy + PyMySQL，仓储在启动时创建表。切换数据库不会迁移已有SQLite会话、文件元数据或保单；请在新数据库创建新会话并导入演示资料。

1. 启动Docker Desktop。在本地`.env`填写`MYSQL_PASSWORD`和`MYSQL_ROOT_PASSWORD`，不要提交。
2. 项目目录运行 `docker compose up -d mysql`，然后 `docker compose ps` 确认healthy。
3. `.env`填写 `DATABASE_URL=mysql+pymysql://finance:URL_ENCODED_PASSWORD@127.0.0.1:3306/finance_agents?charset=utf8mb4`。密码包含特殊字符时先URL编码。
4. 重启API；模型和采集器使用同一DSN，MCP子进程通过环境配置连接同数据库。
5. 设置专用测试数据库的`MYSQL_TEST_URL`后运行 `python -m pytest tests/test_mysql_integration.py -q`；此测试会创建演示记录，只用于测试库。

建表：`runtime_sessions/runs/requests/consents/events`保存会话、业务检查点、幂等、授权和审计；`uploaded_documents`存文件元数据与提取缓存；`claim_policy_history`存用户保单历史；`market_records/watermarks/revisions/ingestion_logs`存行情；`tax_policy_versions`与`tax_policy_source_observations`存审核规则与变化观测（带斜线的表名使用同一前缀，例如runtime_runs）。文件字节仍保存在`data/runtime/documents`，备份数据库时也应备份文件。

停止：`docker compose stop mysql`；数据留在命名卷。没有默认删库脚本，不需要重置卷。

当前主机Docker daemon未运行，MySQL真实连接尚未验证；SQLite、MySQL SQL分支与可选集成入口不等同于真实MySQL端到端验收。首版用`create_all`，后续正式修改表结构需Alembic迁移，不能用自动建表冒充迁移能力。

已有脱敏保单可通过`ClaimsStore.put_policy(user_id, policy)`导入当前会话用户的历史记录。policy包含policy_id、policy_version、生效起止日期、data_mode=redacted、source_uri和已审核clauses（每条包含text、incident_types、excluded）。工作台显示服务端签发的user_id；这个导入是本地管理员数据准备操作，不是允许HTTP请求伪造身份。条款缺失时返回不可用；上传凭证不会自动生成历史保单或推测保障条款。
