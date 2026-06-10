# 玑渡 GIDO 排障 SOP（标准操作流程）

面向：**联调 玑渡 GIDO + DolphinScheduler + Flink 时，按现象快速定位根因并恢复**。与部署步骤分离，部署见 **[DEPLOYMENT_SOP.md](./DEPLOYMENT_SOP.md)**。

**相关文档**

| 文档 | 用途 |
|------|------|
| **[integration-troubleshooting.md](./integration-troubleshooting.md)** | Dolphin / Flink Compose、镜像、健康检查等**环境侧**条目 |
| **[OPS_EVOLUTION_AND_TROUBLESHOOTING.md](./OPS_EVOLUTION_AND_TROUBLESHOOTING.md)** | 历史演进、代码改动与根因**归档** |
| 本文 | **按现象操作的排障 SOP**（含 2026-05-20 会话沉淀） |

---

## 0. 排障顺序（通用）

1. **区分「连不上」与「鉴权/业务失败」**：能收到 HTTP 响应（含 401/502）说明 TCP/路由基本可达。
2. **用与后端相同的环境自测**：`docker exec gido-backend` 内 `curl`，不要用仅宿主机可用的地址（容器内 `127.0.0.1` 指向容器自身）。
3. **核对配置优先级**（Dolphin）：**工作空间「空间设置」> 系统管理「平台集成」> 环境变量**（`GIDO_DS_*` / `DS_*`）。库中 **无效旧 Token 会覆盖 `.env`**，见 §1。
4. **看后端日志**：`docker logs gido-backend --tail 200`（发布 DS、数据源同步、SQL 节点绑定均有关键字）。
5. **改代码或 compose 后**：重建/重启对应容器（`docker compose build backend && docker compose up -d backend`）。

---

## 1. Dolphin：测试连接 / 同步报 401 Unauthorized

### 1.1 现象

- 平台集成或工作流相关接口：`401 Client Error: Unauthorized for url: .../dolphinscheduler/projects?...`
- `curl -i` 返回 **`HTTP/1.1 401`**，**`Content-Length: 0`**（无 JSON body）

### 1.2 含义（不是「网络错误」）

- HTTP 已到达 Dolphin API；**`LoginHandlerInterceptor` 未通过 `queryUserByToken`**。
- 响应头 **`Date` 为 GMT** 是 HTTP 规范，**与东八区令牌时间无关**，勿用 `Date` 判断令牌是否过期。

### 1.3 Token 配置来源（GIDO 实际取值）

后端请求头为 **`token: <值>`**（见 `backend/app/services/dolphin.py` → `DSClient`）。

合并逻辑见 `backend/app/services/ds_runtime.py`：

| 层级 | 说明 |
|------|------|
| 环境变量 | `GIDO_DS_TOKEN` / `DS_TOKEN`（经 `settings`） |
| 平台集成（库） | `platform_integration.ds_token` **非空时覆盖环境变量** |
| 工作空间（库） | `workspace_platform_integration.ds_token` **非空时再覆盖** |

**注意**

- 集成页 **Token 留空点保存** → **不会更新**库中旧 Token（仍为过期/错误串 → 持续 401）。
- 集成 API：**传 `ds_token: ""`** → 库中 Token **清空**，回退环境变量（`admin_integration.py`）。
- 重建过 Dolphin / 换过 PG 后，旧 Token 必然失效，须 **新建令牌** 或 **清空库中 Token** 后仅用环境变量。

### 1.4 操作步骤（推荐顺序）

**① 海豚 UI 核对令牌**

1. 打开 `http://<host>:12345/dolphinscheduler/ui`（勿把 `/ui` 写进 API 根路径）。
2. **安全中心 → 令牌管理 → 新建**（用户需有项目权限，如 `admin`）。
3. 确认 **失效时间晚于当前时间**；若出现 **失效时间早于创建时间**（例如失效 17:24:52、创建 17:24:57），该令牌等价于 **已过期**，必 401。

**② 与 GIDO 对齐**

- **系统管理 → 平台集成 → Dolphin**：粘贴 **完整新 Token** 保存；或保存 **空 Token** 以清空库记录后，在仓库根 `.env` 配置 `GIDO_DS_TOKEN=`。
- **工作空间 → 空间设置**：若单独配置了 DS Token，会覆盖全局，需一并检查。

**③ 重启后端**

```bash
cd gido
docker compose restart backend
# 或 build 后 up -d
```

**④ 容器内自测（与平台一致）**

```bash
# 替换 <TOKEN>；部分环境空 token 可 200，以实际为准
docker exec gido-backend sh -c \
  'curl -sS -i -H "token: <TOKEN>" \
   "http://host.docker.internal:12345/dolphinscheduler/projects?pageNo=1&pageSize=1" | head -20'
```

| 结果 | 结论 |
|------|------|
| HTTP **401**，body 空 | Token 无效/过期/库中仍为旧值 |
| HTTP **200** 且 JSON **`"code":0`** | Token 与 URL 正确；若平台仍 401 → 查工作空间是否另有 Token 覆盖 |
| HTTP **200** 且 **`code != 0`** | 已鉴权，看 `msg` 业务错误 |

**⑤ 本环境曾出现「清空错误 Token 后恢复」**

- 现象：`.env` 与库中均为 **错误/过期 Token** → 401。
- 处理：清空 `GIDO_DS_TOKEN`、集成页 **清空 Token 并保存**、重启 backend；若海豚侧 **未启用 API 令牌校验**，空 `token` 头也可能返回 200（**仅限内网开发**，生产务必使用有效令牌）。

---

## 2. Dolphin：Master ExitCode 137 /「Master 节点不存在」

### 2.1 现象

- `docker inspect ... | grep ExitCode` → **`"ExitCode": 137`**
- 监控中心 **Master 节点不存在**；`docker ps` 仅有 api/worker/alert，**无 master** 或 master **Exited**

### 2.2 根因

- **137 = SIGKILL**，几乎都是 **OOM**（容器 `mem_limit` 或 Docker Desktop 总内存不足）。

### 2.3 处理（按顺序）

1. **Docker Desktop → Resources**：内存 **≥ 8GB**（同机跑 GIDO + Flink + Doris 建议 **12GB+**）。
2. 使用全栈编排 **`dockerFile/docker-compose.platform.yml`**（Master **`mem_limit: 1536m`**、**`JAVA_TOOL_OPTIONS=-Xmx1024m`**、`restart: unless-stopped`）：
   ```bash
   ./start-platform.sh
   docker compose -f docker-compose-platform.yml ps
   docker logs platform-ds-master --tail 50
   ```
3. 确认 OOM：`docker inspect <master容器名> --format '{{.State.OOMKilled}}'`
4. 仍 137：暂时停 Flink/其他占内存服务，或将 `-Xmx` 降到 **768m** 后 **force-recreate**。

### 2.4 与「Master BUSY、Pod 0/1 Running」区分

- 日志 **`SystemMemoryUsedPercentage` > 0.7**、**`serverStatus=BUSY`** → **负载保护**，不是 137；见 **K8s** `k8s/legacy/dolphinscheduler.yaml` 中 `server-load-protection` 阈值调整说明，及 **[integration-troubleshooting.md](./integration-troubleshooting.md) §3**。

---

## 3. 发布 DS：SQL 节点在 Dolphin 里变成 SHELL

### 3.1 现象

- GIDO 节点类型为 **SQL**，Dolphin 工作流里任务类型为 **SHELL**。
- 或点击 **发布 DS** 直接报错：**「SQL 节点未能同步为 Dolphin SQL 任务」**（严格发布，已阻断静默降级）。

### 3.2 根因归纳

| 原因 | 说明 |
|------|------|
| 无数据源 | 节点未配 `datasource_id`，且 **工作空间未设默认数据源**（仓库已修复发布前 `enrich_dag_from_db` 继承默认源） |
| 数据源未进 Dolphin | 保存数据源时 **`dolphin_sync` 非 ok**；JDBC 未挂载等 |
| 类型不支持 | 非 mysql/postgresql/doris 无法注册 Dolphin JDBC |
| 后端未更新 | 仍为旧镜像，发布逻辑未含严格校验 / Doris 注册 |

### 3.3 操作步骤

1. **空间设置**：配置 **默认数据源**（Doris/仓库源）。
2. **数据源**：保存后看接口返回 **`dolphin_sync: { ok: true }`**；Dolphin UI **数据源中心** 是否存在 `gido_ds_*`。
3. **节点配置**：SQL 节点可显式指定数据源。
4. **重新发布 DS**；失败时看响应 **`ds_task_sync`** 或后端日志：
   - `DS SQL 节点绑定: ... dolphin_datasource_id=...`
   - `数据源同步到 Dolphin 失败: ...`
5. **重建后端**后再发布：
   ```bash
   cd gido && docker compose build backend && docker compose up -d backend
   ```

### 3.4 Doris 注册说明

- 同步时优先 **DORIS** 类型，失败再试 **MYSQL**；默认端口 **9030**；空库名可用 **`default_cluster`**（见 `dolphin.py`）。

---

## 4. GIDO 后端容器 Restarting

### 4.1 现象

- `gido-backend` **Restarting (1)**，前端 **502** 或 Nginx 错误页。

### 4.2 常见根因

1. **`init_db` / 启动迁移失败**（PostgreSQL 账密、库不存在、连错实例）。
2. **宿主机 5432 多台 PG**：容器 `host.docker.internal:5432` 连到 **非预期实例**（见 OPS §1）。
3. **`DATABASE_URL` 密码特殊字符**未 URL 编码（`!` → `%21`），或应改用 **`INFRA_GIDO_DB_*`** 明文密码。

### 4.3 操作

```bash
docker logs gido-backend --tail 120
```

按日志修正仓库根 `.env` 中 **`GIDO_DATABASE_URL`** 或 **`INFRA_GIDO_DB_*`** 四项，再 `docker compose -f docker-compose-platform.yml up -d backend`。

---

## 5. 工作流：删除、发布、列表展示

### 5.1 删除工作流须同步删 Dolphin

- 平台删除已发布流程时，后端会 **下线定时 → 删流程定义**（`delete_process_definition`）。
- UI **Popconfirm** 会提示是否含 Dolphin 流程；失败时看接口 **`dolphin_deleted` / `dolphin_note`**。

### 5.2 保存 vs 发布

- **保存**：仅平台草稿；**周期调度在 Dolphin**，平台内 APScheduler 不跑工作流 Cron。
- **发布 DS**：推送 DAG + Cron；列表 **「待发布」** 表示 `needs_ds_republish`。
- 保存 DAG 时用 **`{ ...prevDag, ...fromEditor }`**，避免冲掉 **`ds_process_code`**。

### 5.3 列表表头折行

- 工作流表支持 **表头右侧拖拽列宽**，宽度按工作区存 **`localStorage`**：`gido.workflow.tableCols.w{workspaceId}`。
- 若「最近保存人」仍两行：在该列 **向右拖宽**，或删除上述 localStorage 键后刷新。

---

## 6. 前端构建失败（TypeScript）

### 6.1 含 JSX 的 Hook 文件须为 `.tsx`

- 例：`src/hooks/useResizableTableColumns.tsx`（不可使用 `.ts` 写 JSX）。

### 6.2 类型与 API 对齐

- 工作空间数据源上下文需包含 **`warehouse_datasource_id`**（`workspaceDatasource.ts`），与后端字段一致后再 `npm run build`。

```bash
cd gido/frontend && npm run build
```

---

## 7. 2026-05-20 会话案例速查

| # | 现象 | 根因 | 处理 | 状态 |
|---|------|------|------|------|
| 1 | DS 测试连接 401 | 库/环境中 **过期或错误 Token**；令牌 **失效时间早于创建时间** | 新建令牌或 **清空集成 Token** + 修正 `.env` + 重启 backend；`curl` 验证 | 已恢复 |
| 2 | Master **ExitCode 137** | OOM | 加大 Docker 内存；compose Master 内存上限与 `-Xmx`；`OOMKilled` 排查 | 已文档化 |
| 3 | SQL 发布为 SHELL | 默认源/同步失败/旧后端 | 空间默认源 + `dolphin_sync` + 严格发布报错 + 重建 backend | 需按环境复测 |
| 4 | `gido-backend` Restarting | PG 连接/迁移 | `docker logs` + 修正 `GIDO_DATABASE_URL` | 视环境 |
| 5 | 前端 `npm run build` 失败 | `.ts` 含 JSX / 类型缺失 | 改 `.tsx`、补类型 | 已通过 build |
| 6 | 工作流表头两行 | 列宽不足 | 拖拽列宽或清 localStorage | 已实现 |
| 7 | 删工作流 Dolphin 残留 | 未调 DS 删除 API | 现删除流程会调 `delete_process_definition` | 已实现 |

---

## 8. 最小验证清单（联调通过）

- [ ] `curl` / 平台集成：**DS projects 接口 200 + code 0**
- [ ] `docker ps`：Dolphin **master Up**（非 Exited 137）
- [ ] GIDO **backend Up**，`/health` 正常
- [ ] 数据源保存 **`dolphin_sync.ok`**
- [ ] 工作流 **发布 DS** 成功，Dolphin 中 SQL 任务类型正确
- [ ] 前端 **已构建并部署** 最新镜像

---

## 9. 维护

- 新案例：在 **§7 表格**追加一行，并将可复用步骤沉淀到 **§1–§6** 对应小节。
- Compose / 后端逻辑变更时，同步 **[integration-troubleshooting.md](./integration-troubleshooting.md)** 与 **[OPS_EVOLUTION_AND_TROUBLESHOOTING.md](./OPS_EVOLUTION_AND_TROUBLESHOOTING.md)**。
