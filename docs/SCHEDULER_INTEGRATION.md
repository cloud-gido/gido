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
| 实例同步 | `gido/backend/app/services/dolphin_instance_sync.py` | 工作流/节点实例与 DS 对齐，处理 `scheduler_lost` |
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

### 4.2 运维中心两层视图

1. **工作流实例列表**：节点总数、运行中/失败数、当前节点、耗时等聚合字段  
2. **节点实例下钻**：展示所属工作流实例上下文，避免与外层列表重复

**API（节选）**：`/operation/workflow-instances`、`/operation/node-instances`、停止/刷新/重跑/重试失败节点等。

### 4.3 同步与回调

- 后台轮询 + DS 回调（`POST /scheduler/callback/dolphin`）更新实例状态  
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

完整列表见根目录 `.env.example` 与 `gido/backend/.env.example`。

---

## 6. 告警与 DS Alert 插件的区别

| | GIDO 告警中心 | DS 内置 Alert 插件 |
|--|---------------|------------------|
| 入口 | GIDO Batch → 告警中心 | DS 管理台（用户不可见） |
| 数据来源 | GIDO 实例同步、任务失败事件 | DS 内部 |
| 通知 | 邮件 / Webhook / 飞书 / 企微（可配置） | DS 插件配置 |

GIDO 告警以**工作空间业务语义**呈现（工作流名、节点名、业务日期、日志摘要）。通知配置见 [ALERT_NOTIFICATION.md](./ALERT_NOTIFICATION.md)。

---

## 7. 已知局限与路线图

| 项 | 现状 | 建议 |
|----|------|------|
| DS Token 持久化 | K8s 示例 DS 可能使用 emptyDir | 生产使用 PVC + 定期轮换 Token |
| 引擎抽象 | 仅 Dolphin 完整实现 | 可扩展 Airflow / 自研引擎 |
| E2E 测试 | 单元测试为主 | CI 增加 DS Testcontainers 或 Mock |
| 补数 | 模型与 API 演进中 | 与实例中心统一 UX |

---

## 8. 相关文档

| 文档 | 说明 |
|------|------|
| [ALERT_NOTIFICATION.md](./ALERT_NOTIFICATION.md) | 告警通知渠道配置 |
| [PRODUCT_MATURITY.md](./PRODUCT_MATURITY.md) | Batch 模块完整度 |
| [gido/docs/TROUBLESHOOTING_SOP.md](../gido/docs/TROUBLESHOOTING_SOP.md) | DS 401、Master 137 等排障 |
| [gido/docs/OPEN_SOURCE.md](../gido/docs/OPEN_SOURCE.md) | 开源发布与密钥自查 |
