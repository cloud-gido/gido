# GIDO Batch 调度集成架构

> 璇玑指引 · 数据有渡 — **GIDO 为调度与运维的事实来源**，DolphinScheduler 作为隐藏执行引擎。

本文说明 GIDO Batch 如何与 Apache DolphinScheduler（以下简称 DS）集成，以及工作流生命周期、实例中心与运维操作的职责划分。面向部署、二次开发与 GitHub 贡献者。

---

## 1. 设计原则

| 原则 | 说明 |
|------|------|
| **GIDO 面向用户** | 工作流定义、发布审批、实例列表、运维、告警、补数均在 GIDO UI 完成 |
| **DS 面向执行** | 定时触发、任务分发、Worker 执行、底层日志；**不向终端用户暴露 DS 控制台** |
| **可替换引擎** | `scheduler_engine` 抽象层预留多引擎；当前默认实现为 `dolphin` |
| **快照同步** | 实例与节点状态由 GIDO 持久化，并周期性/回调与 DS 对齐 |

```
用户 / 运维
    │
    ▼
┌─────────────────────────────────────┐
│  GIDO Batch（工作流 / 运维 / 告警）   │
│  workflow · operation · alert API   │
└──────────────┬──────────────────────┘
               │ scheduler_engine
               ▼
┌─────────────────────────────────────┐
│  DolphinScheduler（隐藏执行引擎）    │
│  定义同步 · 调度 · Worker · 日志     │
└─────────────────────────────────────┘
```

---

## 2. 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| DS 客户端 | `gido/backend/app/services/dolphin.py` | HTTP API 封装、Token 鉴权、友好错误翻译 |
| 运行时配置 | `gido/backend/app/services/ds_runtime.py` | 环境变量 / 平台集成 / 工作空间 Token 合并 |
| 调度引擎接口 | `gido/backend/app/services/scheduler_engine/base.py` | `SchedulerEngine` 协议：发布、上下线、暂停/恢复、触发、停止、重试、日志 |
| DS 引擎实现 | `gido/backend/app/services/scheduler_engine/dolphin.py` | 将 GIDO 工作流映射为 DS 流程定义 |
| 实例同步 | `gido/backend/app/services/dolphin_instance_sync.py` | 工作流/节点实例与 DS 对齐；打开实例中心同步本空间最近实例（含失败） |
| 运维操作 | `gido/backend/app/services/scheduler_ops.py` | 停止、刷新、重跑、失败节点重试、结构化日志 |
| 发布 | `gido/backend/app/services/workflow_ds_publish.py` | 发布到 DS、Cron、上线状态 |
| API | `workflow.py` · `operation.py` · `scheduler.py` | 生命周期、运维、回调与诊断 |

---

## 3. 工作流生命周期

GIDO 工作流在业务侧有明确状态，与 DS 定义/调度状态对应：

| GIDO 状态 | 用户可见 | 说明 |
|-----------|----------|------|
| **草稿** | 草稿 | 未发布或已编辑未重新发布 |
| **已上线** | 已上线 | 已发布且调度激活（`is_active=true`） |
| **已暂停** | 已暂停 | 定义仍在线，Cron 暂停 |
| **已下线** | 已下线 | DS 定义下线，不再调度；删除前须先下线 |

**API（节选）**

| 操作 | 方法 | 路径 |
|------|------|------|
| 发布 | `POST` | `/workflows/{id}/publish` |
| 暂停调度 | `POST` | `/workflows/{id}/pause` |
| 恢复调度 | `POST` | `/workflows/{id}/resume` |
| 下线 | `POST` | `/workflows/{id}/offline` |
| 删除 | `DELETE` | `/workflows/{id}`（已发布须先下线） |

---

## 4. 实例中心与运维

### 4.1 数据模型快照字段

`WorkflowInstance` / `NodeInstance` 持久化调度引擎快照，便于在 DS 不可达时仍展示最近状态：

- `scheduler_project_id` / `scheduler_definition_id` / `scheduler_definition_version`
- `scheduler_run_key` / `scheduler_state_raw` / `scheduler_error`
- `last_synced_at`

### 4.2 实例中心的两层视图

1. **工作流实例列表**：节点总数、运行中/失败数、当前节点、耗时等聚合字段
2. **实例运行图**：点「运行图」下钻到这次运行当时的 DAG，每个节点带状态、日志与重试/终止

节点明细原先还有一份表格视图，与运行图重复，已移除；告警里的「打开实例运行图」直接落到运行图。

**API（节选）**：`/operation/workflow-instances`、`/operation/workflows/{wf}/instances/{id}/dag`、
`/operation/node-instances/{id}/log|kill|retry`、停止/刷新/重跑/重试失败节点等。

### 4.3 同步与回调

- 后台轮询（约 15s）+ DS 回调（`POST /scheduler/callback/dolphin`）更新实例状态；失败时立刻写告警并推飞书（工作流级一张卡片）
- 打开实例中心会同步当前工作空间已发布工作流的最近实例（含失败），无需先点「同步」
- `POST /scheduler/ds/sync-instances?workspace_id=`：空间开发者可同步本空间；不传 `workspace_id` 时仅平台管理员可全量同步
- 发布默认：任务 `failRetryTimes=3`（显式 0 表示不重试）、`timeoutFlag=OPEN`  
- 匹配策略：项目 + 流程定义 + 实例 ID，降低误匹配  
- DS 侧实例消失时标记 `scheduler_lost`，提示运维刷新或核对 Token

---

## 5. DolphinScheduler 配置

### 5.1 Token 优先级

| 层级 | 来源 |
|------|------|
| 1（最高） | 工作空间「空间设置」中的 DS Token |
| 2 | 系统管理「平台集成」中的 DS Token |
| 3 | 环境变量 `GIDO_DS_TOKEN` / `DS_TOKEN` |

库中**非空但已过期**的 Token 会覆盖环境变量，导致持续 401。处理见 [gido/docs/TROUBLESHOOTING_SOP.md](../gido/docs/TROUBLESHOOTING_SOP.md) §1。

### 5.2 部署方式

| 场景 | 说明 |
|------|------|
| Compose 全栈 | `./start-platform.sh` 内置 Dolphin |
| K3s / EKS | 外置 DS，参考 `k8s/legacy/dolphinscheduler.yaml`、`k8s/deploy-dolphinscheduler-k3s.sh` |
| 生产 | DS 元库建议 PVC；Token 定期轮换 |

### 5.3 环境变量（节选）

| 变量 | 说明 |
|------|------|
| `GIDO_DS_API_URL` | DS API 根路径（不含 `/ui`） |
| `GIDO_DS_TOKEN` | API 令牌 |
| `GIDO_DS_ENABLED` / `DS_ENABLED` | 是否启用调度集成 |
| `GIDO_PUBLIC_URL` | 浏览器入口回退值；优先使用「平台集成 → 站点入口」 |

完整列表见根目录 `.env.example` 与 `gido/backend/.env.example`。

---

## 6. 告警与 DS Alert 插件的区别

| | GIDO 告警中心 | DS 内置 Alert 插件 |
|--|---------------|------------------|
| 入口 | GIDO Batch → 告警中心 | DS 管理台（用户不可见） |
| 数据来源 | GIDO 实例同步、任务失败事件 | DS 内部 |
| 通知 | 邮件 / Webhook / 飞书 / 企微（可配置） | DS 插件配置 |

GIDO 告警以**工作空间业务语义**呈现（工作流名、失败节点、业务日期、日志摘要）。同一工作流失败默认 15 分钟冷却；节点告警不单独推飞书。告警中心默认隐藏推送起点之前的历史入库，并提示「本空间无已发布作业 / 飞书未开 / 未配站点入口」。通知配置见 [ALERT_NOTIFICATION.md](./ALERT_NOTIFICATION.md)。

---

## 7. 并发与故障隔离（生产硬约束）

线上出过一次 CPU 打满：`dw_workflow_instances.scheduler_run_key` 有唯一索引，多个并发采集插同一个 key，在 Postgres 上后插入的一方要**阻塞等前一个事务提交**才知道算不算冲突（`wait_event = transactionid`），而那个事务里还夹着对引擎的 HTTP 调用。请求连着 DB 会话一起堆积，连接池被吃干，最后连纯读的告警中心都打不开。

由此定下四条硬约束，改这块代码时必须遵守：

| 约束 | 原因 | 落点 |
|------|------|------|
| 互斥锁加在**被调方**，不加在调用方 | 守调用方只能守住你记得的那几个。`collect_runs` 有页面兜底、手动采集等多个入口，漏一个就等于没锁 | `run_collector.collect_runs`、`sync_worker.enqueue_sync_record` |
| 拿不到锁**立即跳过/拒绝**，不排队 | 排队本身就是故障形态——积压的采集轮次会把池吃干 | 返回 `collected=False` / 抛 `RuntimeError` |
| 带唯一约束的表**不许先查再插** | 先查再插在并发下必然双双命中，轻则 500 重则锁convoy。要让数据库裁决：插进去，撞了再退回已存在那行 | `services/db_idempotent.insert_or_get` |
| 被前端轮询的接口**只读** | 引擎一慢就把请求和 DB 会话一起挂住，页面越刷越糟 | `/operation/overview`、`/operation/instances`、`/alerts` |

另外两点容易踩：

- **`claim_once` 只是优化，不是正确性保证。** 它靠 Redis：本地没配 `REDIS_URL` 时返回 `None`（等于没保护），而生产 `SHARED_STATE_REQUIRED=true` 时 Redis 不可用会**抛异常**。后台任务里直接调它且不兜异常，Redis 一抖就会让定时工作流、实例采集、告警重投、基线巡检**集体停摆**。所以定时任务统一走 `shared_state.claim_or_proceed`：只有明确被别的副本抢到才跳过，其余情况一律放行，由 advisory 锁保证互斥。共享状态的故障不该扩散成核心功能的故障。
- **advisory 锁走 `engine.raw_connection()`**，持锁期间占着一个池连接。别嵌套加锁，也别在持锁期间做长网络往返。

连接池在 `core/config.py` 显式配置（`DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT`）。`pool_timeout` 是关键：没有它，池满之后请求**无限期**等连接，一个慢东西就能拖垮无关业务；有它则是快速失败，故障被局部化。池上限对齐 uvicorn 跑同步接口的线程池（anyio 默认 40），让池不成为瓶颈。换部署形态前先核对「副本数 × worker 数 × (pool_size + max_overflow)」是否还在 Postgres `max_connections` 之内——生产是 2 副本单进程 + 专用 RDS（80 个连接，余量充足），自建 PG 默认只有 100。

---

## 8. 已知局限与路线图

| 项 | 现状 | 建议 |
|----|------|------|
| DS Token 持久化 | K8s 示例 DS 可能使用 emptyDir | 生产使用 PVC + 定期轮换 Token |
| 引擎抽象 | 仅 Dolphin 完整实现 | 可扩展 Airflow / 自研引擎 |
| E2E 测试 | 单元测试为主 | CI 增加 DS Testcontainers 或 Mock |
| 补数 | 模型与 API 演进中 | 与实例中心统一 UX |
| 试跑占用池连接 | 试跑 SQL / 数据集成同步在请求内同步跑，整个外部 I/O 期间占着一个池连接，且用户 SQL 没有语句超时 | 改为后台任务 + 轮询进度；并给用户 SQL 加语句超时 |
| CDC 长期占用池连接 | CDC 的跨副本互斥是正确的——`_cdc_worker_loop` 开头取 per-task advisory 锁（非阻塞），抢不到就退出，`_active_workers` 只是进程内记账。但锁持有到 worker 结束，**每个运行中的 CDC 任务在归属副本上长期占一个池连接**；非归属副本的管理器每 15 秒还会为每个任务白起一次线程 + 一次连接借还 | 任务数多起来后改成租约 + 心跳续期（参考 `sync_worker` 的 `reclaim_stale_running`），锁只在续期瞬间持有 |
| 并发时序未覆盖 | `wait_event = transactionid` 的阻塞行为要真起并发打真库才能复现，现有单测只锁设计属性 | CI 增加 Postgres Testcontainers 并发用例 |

---

## 9. 相关文档

| 文档 | 说明 |
|------|------|
| [ALERT_NOTIFICATION.md](./ALERT_NOTIFICATION.md) | 告警通知渠道配置 |
| [PRODUCT_MATURITY.md](./PRODUCT_MATURITY.md) | Batch 模块完整度 |
| [gido/docs/TROUBLESHOOTING_SOP.md](../gido/docs/TROUBLESHOOTING_SOP.md) | DS 401、Master 137 等排障 |
| [gido/docs/OPEN_SOURCE.md](../gido/docs/OPEN_SOURCE.md) | 开源发布与密钥自查 |
