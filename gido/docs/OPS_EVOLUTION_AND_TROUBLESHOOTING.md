# 玑渡 GIDO 运维与排障梳理（演进记录）

本文档汇总自 **PostgreSQL 元数据迁移 / Docker 与 Dolphin 对齐** 以来，在部署、调度、数据源与 Doris 联调中反复出现的问题、根因与落地改动，便于团队整体回顾与新人上手。

---

## 1. 元数据库（PostgreSQL）与 Docker 网络

### 现象

- `gido-backend` 持续 **Restarting**，`init_db` 报 **`password authentication failed for user "root"`** 或 **`governance`**。
- 或 `RuntimeError`：无法使用当前元数据库连接（`DATABASE_URL` 或 `INFRA_GIDO_DB_*`）连接数据库。

### 根因归纳

1. **宿主机 `5432` 上不止一台 PostgreSQL**  
   例如同时存在 **`dolphinscheduler-postgresql`**（映射 `0.0.0.0:5432`）与 **`dolphinscheduler-docker-postgres-1`**（仅容器内 `5432`，对应仓库 `dockerFile/docker-compose.dolphin.yml` 的 `postgres` 服务）。  
   容器内使用 **`host.docker.internal:5432`** 时，实际连到的是 **占宿主机 5432 的那台**，其账号未必是仓库文档里的 `root` / `DolphinPgDev!72`。

2. **PostgreSQL 数据目录已初始化后，改 `POSTGRES_PASSWORD` 不会生效**  
   须与 **当前数据卷里真实密码** 一致，或重建卷（会丢数据，需谨慎）。

3. **`DATABASE_URL` 中密码特殊字符**  
   例如 `!` 在 URL 中须编码为 **`%21`**。若改用 **`INFRA_GIDO_DB_SERVICE_PASSWORD`**，密码为**明文环境变量**，由后端 `quote_plus` 写入连接串，**无需**在 URL 里手工编码。

4. **只配置了部分 `INFRA_GIDO_DB_*`**  
   须四项齐全（`SERVICE_URL`、`SERVICE_USER`、`SERVICE_PASSWORD` 含空串、`INFRA_GIDO_DB_URL` 库名），否则会启动失败；详见 `backend/app/core/config.py` 报错文案。

### 仓库侧约定与改动

- **`dockerFile/docker-compose.dolphin.yml`**：为唯一 Dolphin PG 服务 **`postgres` 增加 `ports: "5432:5432"`**，使宿主机与 `host.docker.internal:5432` 与文档账密一致；并附 **`dockerFile/remove-legacy-postgres-on-5432.sh`**，用于移除占用 5432 的旧容器 **`dolphinscheduler-postgresql`**（执行前请确认无重要数据）。
- **`gido/docker-compose.yml`**：注释说明单 PG 与端口冲突处理。
- **`backend/app/core/database.py`**：PostgreSQL 自动建库、失败时更明确的报错提示。
- **`backend/app/core/config.py`**：支持 **`INFRA_GIDO_DB_*`** 拆分元数据库配置，与运维 Secret 注入对齐。

---

## 2. 前端 502 与错误展示

### 现象

- 保存数据源等操作时，界面弹出 **整段 Nginx HTML**（`502 Bad Gateway`）。

### 根因

- 前端 Nginx 将 `/api` 反代到 **`gido-backend`**；后端未就绪或崩溃时返回 **502 HTML**，原样进入 Ant Design `message` 难以阅读。

### 改动

- **`frontend/src/api/request.ts`**：若响应为 502/503/504 且 body 为 HTML 错误页，替换为简短 **`detail` 中文提示**，避免整页 HTML 进弹窗。

---

## 3. `init_db` 与 `dw_streaming_jobs` 表顺序

### 现象

- `migrate_dw_streaming_job_history` 报 **`relation "dw_streaming_jobs" does not exist`**。

### 根因

- `init_db.py` 在 **`Base.metadata.create_all`** 前未注册 **`app.api.streaming`** 中的 ORM 模型，导致 **`dw_streaming_jobs`** 未被创建；后续迁移 SQL 依赖该表即失败。  
- **`app/main.py`** 路径因已 `import streaming`，故仅 **`init_db.py`** 单独跑时会暴露问题。

### 改动

- **`backend/init_db.py`**：在 `create_all` 前增加 **`import app.api.streaming`**（与迁移回归测试一致）。

---

## 4. Doris 在「数据开发 / SQL 编辑器」与 Dolphin 中的差异

### 4.1 `WITH RECURSIVE` 与 `version()`

- **Apache Doris 3.1.x** 官方文档：**不支持递归 CTE**；`version()` 返回 **`5.7.99`** 多为 **MySQL 协议兼容占位**，不代表 Oracle MySQL 5.7。
- **结论**：日期维表等勿照搬 PG/MySQL8 的 **`WITH RECURSIVE`**；改用 **数字膨胀 + `date_add`/`days_add`** 等 Doris 支持写法。

### 4.2 `enable_vectorized_engine` 等 PROPERTIES

- 报错 **`Unknown properties: {enable_vectorized_engine=true}`** 时，多为 **当前 Doris 版本已废弃该表属性**；从 `PROPERTIES` 中删除即可。

### 4.3 在 Dolphin 里用「MYSQL」类型跑 Doris DDL

- 任务参数里 **`ENGINE=OLAP`、`DUPLICATE KEY` 等** 为 **Doris 专有语法**；若长期用 Dolphin SQL 节点跑 Doris，应优先使用 **Doris 类型数据源 / 官方推荐方式**，避免与 MySQL 方言混淆（具体以当前 Dolphin 与 Doris 版本文档为准）。

---

## 5. 工作流：Cron、发布与业界对齐

### 现象

- 「每分钟 / 每小时」**不生效**；或保存后 Dolphin 侧调度未更新。
- **`dw_streaming_jobs` NPE**（已另节说明）之外的 **工作流侧** 问题。

### 根因归纳

1. **开启 Dolphin 时，GIDO 内 APScheduler 不会注册工作流 Cron**（见 `scheduler.py`），周期执行完全依赖 **Dolphin**。
2. **仅「保存」工作流不会把 Cron 推到 Dolphin**；须 **「发布 DS」**，或依赖后续实现的保存后同步逻辑。
3. **DAG 编辑器 `getDAG()` 只返回 `nodes`/`edges`**，若整对象覆盖 **`dag_config`**，会丢失 **`ds_project_code` / `ds_process_code`**，导致无法再同步 Cron。

### 改动（对齐「草稿 / 发布」分层）

- **`backend/app/services/workflow_dag_validate.py`**：发布前 **DAG 结构校验**（非空、边合法、无环、节点归属工作空间）、**Cron 为 5 段**；**`ds_meta.needs_republish`** 标记与清除。
- **`backend/app/services/workflow_ds_publish.py`**：发布时 **合并 `dag_config`**（保留 `ds_meta` 等），避免整份覆盖；发布成功清除 **`needs_republish`**。
- **`backend/app/api/workflow.py`**：`publish-to-ds` 统一走 **`publish_workflow_to_ds`**；更新工作流后在已绑定 DS 时 **尝试推送 Cron**；失败回滚会话；**`WorkflowOut`** 增加 **`needs_ds_republish`**、**`updated_at`**。
- **`frontend/src/pages/Workflow.tsx`**：保存时 **`{ ...prevDag, ...fromEditor }`** 合并，保留 DS 元数据；列表展示 **「待发布」**；副标题说明保存/发布分层。
- **`frontend/src/components/CronBuilder.tsx`**：快捷预设与底部 Cron 展示 **联动**（同步内部字段 + 优先展示表单 `value`）。

---

## 6. Dolphin SQL 任务：`anonym@null`、NPE 与数据源同步

### 6.1 `Access denied for user 'anonym@null' (using password: NO/YES)`

- **Dolphin 侧 MySQL JDBC** 在 **`userName` 为空** 时会走 **匿名用户**，与 **GIDO Batch Studio（PyMySQL）** 对「空用户连无认证 Doris FE」的行为不一致，故出现 **同一条 SQL 在 GIDO 能跑、在 Dolphin 失败**。

### 6.2 代码层对齐（Doris 空用户）

- **`backend/app/services/datasource_mysql_user.py`**：  
  **`doris` 且用户名为空** → 对 **Dolphin 同步 JSON** 与 **Studio PyMySQL** 统一使用 **`root`** 作为连接用户名（占位，与多数无认证 Doris 行为一致）；显式填写用户名则仍使用填写值。
- **`backend/app/services/dolphin.py`**：`userName` 使用上述函数。
- **`backend/app/api/studio.py`**、**`datasource.py` 测试连接**：PyMySQL 同样使用该用户名逻辑。
- **校验策略**：**仅 mysql / postgresql 强制必填用户名**；**doris 允许空用户名**。

### 6.3 `SqlParameters.generateExtendedContext` NPE（`datasource: 1`）

- 根因多为 **任务绑定的 Dolphin 数据源 ID 在运行上下文中解析不到**（未发布、ID 过期、跨项目/租户等）。
- **处理**：在 Dolphin 中 **重新选择数据源** 或从 **GIDO 再「发布 DS」**；关注后端日志 **`DS SQL 节点绑定: ... dolphin_datasource_id=...`** 与 Dolphin 数据源中心 **id** 是否一致。
- **`taskParams` 增加 `varPool: []`** 等与 Dolphin 3.2.x 反序列化兼容的字段（见 `dolphin.py`）。

### 6.4 GIDO 数据源「未出现在 Dolphin」

- 条件：**Dolphin 集成开启**、**`DS_URL` + `DS_TOKEN` 配置完整**、类型为 **mysql / postgresql / doris**、**库名等校验通过**。
- **列表接口不返回 `dolphin_sync` 字段**；以 **创建/保存接口返回** 中的 **`dolphin_sync`**（`ok` / `error:…` / `skipped:…`）为准。

### 6.5 发布 DS 后 SQL 节点变成 SHELL

- **设计**：`node_type=SQL` 且能解析到数据源（节点配置 **或** 工作空间默认）且成功同步到 Dolphin → DS **`taskType: SQL`**（脚本、`localParams`、含 `$[...]` 的进 `globalParams`）。
- **常见原因**：
  1. 节点未单独选数据源，且 **空间设置未配置默认数据源**（Studio 试跑可能仍失败；旧版发布曾降级为 SHELL，已修复为与试跑一致继承默认源）。
  2. 数据源类型非 **mysql/postgresql/doris**（如 hive）无法注册 Dolphin JDBC，会降级 SHELL。
  3. 向 Dolphin 注册数据源失败（权限、JDBC jar、重复名称等）— 看后端日志 **`数据源同步到 Dolphin 失败`**。
- **处理**：空间设置设默认数据源 → 在节点「配置」里可显式指定 → **重新「发布 DS」** → Dolphin 中应为 SQL 任务；日志应有 **`DS SQL 节点绑定: ... dolphin_datasource_id=...`**。
- **2026-05-20**：发布路径增加 **严格校验**——若 SQL 节点将降为 SHELL，**直接 `RuntimeError`** 并返回 **`ds_task_sync`** 明细（`workflow_ds_publish.py`），避免静默发布错误类型。

### 6.6 删除工作流时同步删除 Dolphin 流程

- **`workflow.py` DELETE**：在 DS 已启用且存在 **`ds_process_code`** 时，先下线定时再 **`delete_process_definition`**；响应含 **`dolphin_deleted` / `dolphin_note`**。

---

## 7. 相关代码与文档索引

| 主题 | 主要文件 |
|------|-----------|
| PG 建库与连接提示 | `backend/app/core/database.py` |
| Dolphin PG 单实例与清理脚本 | `dockerFile/docker-compose.dolphin.yml`、`dockerFile/remove-legacy-postgres-on-5432.sh` |
| 502 响应处理 | `frontend/src/api/request.ts` |
| `init_db` 注册流表 ORM | `backend/init_db.py` |
| Doris JDBC 用户名占位 | `backend/app/services/datasource_mysql_user.py`、`backend/app/services/dolphin.py`、`backend/app/api/studio.py`、`backend/app/api/datasource.py` |
| 数据源 Dolphin 同步 | `backend/app/services/datasource_dolphin_sync.py`、`backend/app/api/datasource.py` |
| 工作流发布 / DAG / Cron | `backend/app/services/workflow_dag_validate.py`、`workflow_ds_publish.py`、`backend/app/api/workflow.py`、`frontend/src/pages/Workflow.tsx`、`frontend/src/components/CronBuilder.tsx` |
| 部署主 SOP | `docs/DEPLOYMENT_SOP.md` |
| **按现象排障 SOP** | **`docs/TROUBLESHOOTING_SOP.md`** |
| 集成排障 | `docs/integration-troubleshooting.md` |

---

## 8. 2026-05-20 排障实录（摘要）

完整操作步骤与命令见 **[TROUBLESHOOTING_SOP.md](./TROUBLESHOOTING_SOP.md)** §7 案例表。

| 主题 | 要点 |
|------|------|
| DS **401** | 鉴权非网络；清库中旧 Token / 新建令牌；注意失效时间异常 |
| Master **137** | OOM；Docker 内存 + compose `mem_limit` / `-Xmx` |
| SQL→**SHELL** | 默认源 + `dolphin_sync` + 严格发布 + 重建 backend |
| 工作流 UI | 可拖拽列宽；删除同步 Dolphin |

---

## 9. 维护说明

- 若环境与本文描述不一致（例如 Doris 强制非 `root` 账号），请在 **GIDO 数据源中显式填写用户名**，逻辑以显式值为准。
- 新增排障条目：可复用步骤写入 **[TROUBLESHOOTING_SOP.md](./TROUBLESHOOTING_SOP.md)**；代码/根因归档在本文件 **按主题追加小节**，并在 `DEPLOYMENT_SOP.md` 索引中保持链接。
