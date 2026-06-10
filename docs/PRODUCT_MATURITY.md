# GIDO 中台 · 功能完整度梳理

> 璇玑指引 · 数据有渡 — 一站式 **批 / 流 / 服** 数据开发与治理中台  
> 本文描述 **当前代码与默认部署路径** 下的能力边界，便于评估落地与裁剪。

---

## 1. 产品架构

GIDO 是 **单壳三产品** 的中台：

| 子产品 | 路由前缀 | 定位 |
|--------|----------|------|
| **GIDO Batch**（玑渡·批） | `/gido/batch/*` | 离线 SQL、工作流、集成、治理、调度运维 |
| **GIDO Stream**（玑渡·流） | `/gido/stream/*` | Flink SQL / JAR 开发与运维 |
| **GIDO Serve**（玑渡·服） | `/gido/service/*` | SQL 封装 HTTP API、应用授权与监控 |

共用：登录与 RBAC、工作空间、审计、主题、系统管理（集成 / 数据源 / 审批）。

---

## 2. 部署路径与能力对照

| 部署方式 | 命令 / 清单 | Batch 调度 | Stream 提交 | Serve |
|----------|-------------|------------|-------------|-------|
| **Compose 全栈** | `./start-platform.sh` | Dolphin + Kafka + PG | Flink Session + Gateway | 完整 |
| **K3s 最小栈（推荐生产流）** | `k8s/deploy-gido-k3s.sh` | 元库有表，**DS 默认关** | **Flink Operator + 自建 runtime 镜像** | 完整 |
| **Kind 开发栈** | `k8s/apply-gido-stack.sh` | 同左 | Operator JAR；Session 可选 `GIDO_APPLY_FLINK=1` | 完整 |

**当前主推**：K3s/K8s + **Flink Kubernetes Operator 1.15** + **Flink 2.0.1**（`flinkVersion: v2_0`），作业镜像 `gido-flink-sql-runner`。

---

## 3. GIDO Batch · 完整度

| 模块 | 菜单 / API | 状态 | 说明 |
|------|------------|------|------|
| 数据开发 Studio | `/batch/studio` | **可用** | SQL 编辑、运行、结果面板 |
| 工作流 DAG | `/batch/workflow` | **可用** | 可视化编排；发布依赖 Dolphin |
| 调度 / 实例 | scheduler API | **条件可用** | K8s 最小栈 `DS_ENABLED=false` 时仅 UI/元数据，无真实调度 |
| 数据集成 | `/batch/integration` | **可用** | 多源同步；CDC 为 **轮询增量**，非 Debezium 原生 |
| 数据地图 | `/batch/datamap` | **可用** | 表/字段字典、血缘（**基于 SQL 正则**，非 OpenLineage） |
| 数据探查 | `/batch/probe` | **可用** | 采样与统计 |
| 数据质量 | `/batch/quality` | **部分** | 规则引擎可用；执行层偏 **MySQL 协议** 数据源 |
| 运维中心 | `/batch/operation` | **可用** | 实例监控、趋势（依赖 DS 时有真实数据） |
| 发布审批 | `/batch/approval` | **可用** | 批/流共用审批流 |
| 数据源 | 系统管理 | **可用** | PG/MySQL 等；可与 DS 同步账号 |
| RBAC | 系统管理 | **可用** | 角色、权限码、工作空间 |

**Batch 落地缺口（最小 K8s）**：需单独部署 Dolphin（Compose 全栈或 `k8s/legacy/dolphinscheduler.yaml`）才能端到端「发布 → 调度 → 实例」。

---

## 4. GIDO Stream · 完整度

| 模块 | 状态 | 说明 |
|------|------|------|
| Flink SQL 开发 | **可用** | 编辑器、模板（含 CDC→Paimon 示例 SQL） |
| Flink JAR 作业 | **可用** | Operator `FlinkDeployment` 提交 |
| 作业运维 / 状态 | **可用** | 对接 K8s Operator CR |
| 发布审批 | **可用** | 与 Batch 共用 |
| Session / Gateway 路径 | **遗留** | `GIDO_LEGACY_FLINK_SUBMIT=false` 默认关闭；清单在 `k8s/legacy/` |
| CDC→Paimon 端到端 | **EKS 就绪** | 运行时含 Paimon + mysql-cdc + S3 插件；见 [docs/CDC_PAIMON_EKS.md](../docs/CDC_PAIMON_EKS.md) 与 `k8s/eks/` |

**Stream 生产 checklist**：Operator RBAC、`gido-flink-runtime` 镜像、集成里配置 K8s 域与镜像仓库；SQL 作业走 SqlRunner JAR。

---

## 5. GIDO Serve · 完整度

| 模块 | 状态 | 说明 |
|------|------|------|
| 服务概览 | **可用** | 调用量、延迟聚合 |
| API 开发（SQL→REST） | **可用** | 参数化 SQL、调试 |
| 应用管理 AppKey | **可用** | 授权与密钥 |
| 调用监控 | **可用** | Trace、错误 |
| 开放网关 | **可用** | `/open/*` 对外路由 |

Serve 对 **元库 PG + 已注册数据源** 依赖最少，在 K8s 最小栈上 **完整度最高**。

---

## 6. 横切能力

| 能力 | 状态 |
|------|------|
| 多工作空间 | 可用 |
| 审计日志 | 可用 |
| 品牌 / 主题 | 可用 |
| CI（backend pytest + frontend build） | 可用 |
| 分层镜像部署（app 每次构建 / flink runtime 按需） | 可用 |

---

## 7. 已知局限（产品级）

1. **调度**：K8s 默认不 bundled Dolphin，Batch「真调度」需外置 DS。  
2. **集成 CDC**：非 Flink CDC / Debezium 一体化，适合轻量增量。  
3. **血缘**：规则解析，复杂脚本可能不全。  
4. **质量执行**：非所有 JDBC 类型对等支持。  
5. **流 SQL**：复杂 DDL/多语句依赖 SqlRunner 与集群 Catalog 配置。

---

## 8. 仓库清理说明（2026-06）

已移入 **`k8s/legacy/`**：Session Flink、DS/Doris 示例、相关 shell。  
已删除：过时 stub（`docker-compose.kind.override.yml.example`）、过时交接文档 `gido/docs/DEV_HANDOFF.md`。  
**保留**：`gido/backend/tests/`（CI 单元测试）、Compose 内 Flink Session（全栈开发）、`docs/PRODUCT_OVERVIEW.md`（对外介绍）。

---

## 9. 相关文档

- [PRODUCT_OVERVIEW.md](./PRODUCT_OVERVIEW.md) — 界面与 5 分钟体验  
- [FLINK_ARCHITECTURE.md](./FLINK_ARCHITECTURE.md) — 流计算架构  
- [k8s/README.md](../k8s/README.md) — K8s 部署  
- [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md) — 文档索引
