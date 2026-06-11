# GIDO 中台 · 功能完整度梳理

> 璇玑指引 · 数据有渡 — 一站式 **批 / 流 / 服** 数据开发与治理中台  
> 最后更新：2026-06 · 对应仓库 `main`（Flink Operator + S3 制品 + CDC→Paimon EKS）

本文描述 **当前代码与典型部署路径** 下的能力边界，便于评估落地、裁剪与发包说明。

---

## 1. 总览评分（按部署路径）

| 子产品 | Compose 全栈 | K3s 最小栈 | **AWS EKS 生产** |
|--------|:------------:|:----------:|:----------------:|
| **GIDO Batch**（批） | ★★★★☆ | ★★★☆☆ | ★★★☆☆ |
| **GIDO Stream**（流） | ★★★☆☆ | ★★★★☆ | ★★★★★ |
| **GIDO Serve**（服） | ★★★★★ | ★★★★★ | ★★★★★ |
| **横切**（RBAC/审计/主题） | ★★★★★ | ★★★★★ | ★★★★★ |

**解读**

- **Serve** 只依赖元库 + 数据源，三条路径均完整。
- **Stream** 在 **EKS + Operator + S3** 上最完整（制品持久化、CDC→Paimon、checkpoint）。
- **Batch** 强依赖 **DolphinScheduler**；K8s 默认不 bundled DS，调度为最大缺口。

---

## 2. 产品架构

GIDO 是 **单壳三产品** 的中台：

| 子产品 | 路由前缀 | 定位 |
|--------|----------|------|
| **GIDO Batch**（玑渡·批） | `/gido/batch/*` | 离线 SQL、工作流、集成、治理、调度运维 |
| **GIDO Stream**（玑渡·流） | `/gido/stream/*` | Flink SQL / JAR 开发与运维 |
| **GIDO Serve**（玑渡·服） | `/gido/service/*` | SQL 封装 HTTP API、应用授权与监控 |

**共用能力**：登录、多工作空间、RBAC、审计、主题/品牌、数据源、发布审批、系统管理（集成配置）。

---

## 3. 部署路径与能力对照

| 部署方式 | 入口 | Batch 调度 | Stream 提交 | 制品存储 | Serve |
|----------|------|------------|-------------|----------|-------|
| **Compose 全栈** | `./start-platform.sh` | Dolphin 完整 | Flink **Session** + Gateway | 本地 PVC / HTTP | 完整 |
| **K3s 准生产** | `k8s/deploy-gido-k3s.sh` | DS 默认关 | **Operator** + runtime 镜像 | PVC + HTTP（可配 S3） | 完整 |
| **Kind 开发** | `k8s/apply-gido-stack.sh` | 同 K3s | Operator；Session 可选 | 同 K3s | 完整 |
| **AWS EKS 生产** | [CDC_PAIMON_EKS.md](./CDC_PAIMON_EKS.md) | 外置 DS | Operator + **S3 制品** + CDC→Paimon | **S3 持久化** | 完整 |

**流计算主推栈**：Flink Kubernetes Operator **1.15** + Flink **2.0.1**（`flinkVersion: v2_0`），镜像 `gido-flink-runtime`（Paimon + MySQL CDC + S3 插件）。

---

## 4. GIDO Batch · 模块完整度

| 模块 | 前端 | 后端 API | Compose | K8s 最小栈 | 说明 |
|------|:----:|:--------:|:-------:|:----------:|------|
| 数据开发 Studio | ✅ | `/studio` | ✅ | ✅ | SQL 编辑、运行、结果 |
| 工作流 DAG | ✅ | `/workflows` | ✅ | ✅ | 可视化编排；**发布需 DS** |
| 调度 / 实例 | ✅ | `/scheduler` | ✅ | ⚠️ | K8s 默认 `DS_ENABLED=false` |
| 数据集成 | ✅ | `/integration` | ✅ | ✅ | 多源同步；CDC 为**轮询增量** |
| 数据地图 | ✅ | `/datamap` | ✅ | ✅ | 字典 + **SQL 正则血缘** |
| 数据探查 | ✅ | `/probe` | ✅ | ✅ | 采样统计 |
| 数据质量 | ✅ | `/quality` | ⚠️ | ⚠️ | 规则可用；执行偏 **MySQL 协议** |
| 运维中心 | ✅ | `/operation` | ✅ | ⚠️ | 有 DS 才有真实实例数据 |
| 发布审批 | ✅ | `/approvals` | ✅ | ✅ | 批/流共用 |
| 数据源 | ✅ | `/datasources` | ✅ | ✅ | PG/MySQL 等 |
| RBAC | ✅ | `/admin` | ✅ | ✅ | 角色、权限码、工作空间 |

**Batch 生产落地**：K8s/EKS 需外置 Dolphin（Compose 全栈或 `k8s/legacy/dolphinscheduler.yaml`）才能端到端「发布 → 调度 → 实例」。

---

## 5. GIDO Stream · 模块完整度

| 模块 | 状态 | Compose 全栈 | K3s / Kind | EKS 生产 |
|------|------|:------------:|:----------:|:--------:|
| Flink SQL 开发 | ✅ | Session 提交 | **Operator** | **Operator** |
| Flink JAR 作业 | ✅ | Session / 遗留 | **Operator** | **Operator** |
| 作业运维 / 状态同步 | ✅ | ✅ | ✅ | ✅ |
| Flink UI 代理 | ✅ | 直连 JM | ✅ | ✅ |
| 发布审批 | ✅ | ✅ | ✅ | ✅ |
| 多套集群连接配置 | ✅ | ✅ | ✅ | ✅ |
| **S3 制品库**（JAR/SQL） | ✅ | — | 可选 | **推荐** |
| **CDC→Paimon** | ✅ | 需自建 MySQL+仓库 | 需基建 | **文档 + 模板就绪** |
| Session / Gateway | 遗留 | ✅ 默认 | `k8s/legacy/` | 不推荐 |

**Stream 关键配置（EKS）**

| 变量 | 用途 |
|------|------|
| `FLINK_OPERATOR_JAR_S3_PREFIX` | JAR/SQL 制品 S3 前缀 |
| `PAIMON_WAREHOUSE_DEFAULT` | Paimon 仓库存路径 |
| `FLINK_OPERATOR_CHECKPOINT_DIR` | Flink checkpoint（建议 S3） |
| `FLINK_OPERATOR_ARTIFACT_TOKEN` | HTTP 制品拉取校验（S3 未配时必需） |
| `FLINK_OPERATOR_IMAGE` | 含 sql-runner + Paimon + CDC + S3 的运行时镜像 |

详见 [CDC_PAIMON_EKS.md](./CDC_PAIMON_EKS.md)、[FLINK_ARCHITECTURE.md](./FLINK_ARCHITECTURE.md)。

---

## 6. GIDO Serve · 模块完整度

| 模块 | 前端 | 后端 | 全路径 |
|------|:----:|:----:|:------:|
| 服务概览 | ✅ | `/data-service` | ✅ |
| API 开发（SQL→REST） | ✅ | ✅ | ✅ |
| 应用管理 AppKey | ✅ | ✅ | ✅ |
| 调用监控 | ✅ | ✅ | ✅ |
| 开放网关 | ✅ | `/open/v1` | ✅ |

Serve **完整度最高**，EKS 上仅需 PG 元库 + 业务数据源即可独立上线。

---

## 7. 横切与工程化

| 能力 | 状态 | 说明 |
|------|------|------|
| 多工作空间 | ✅ | 隔离脚本、作业、权限 |
| 审计日志 | ✅ | 关键操作留痕 |
| 品牌 / 多主题 | ✅ | 登录、关于页 |
| CI | ✅ | backend pytest + frontend build |
| K3s 分层部署 | ✅ | 应用每次构建 / Flink runtime 按需 |
| EKS 示例清单 | ✅ | `k8s/eks/`（双 IRSA、MySQL Secret） |
| 单元测试 | ✅ | Operator、S3 制品、runtime API 等 |

---

## 8. 生产就绪 vs 待增强

### 已就绪（可上 EKS 生产）

- Flink Operator SQL/JAR 提交与运维
- S3 持久化制品库（JAR/SQL）
- CDC→Paimon 运行时、SQL 模板、IRSA 示例、部署文档
- Serve 全链路
- RBAC、审批、审计、多工作空间

### 条件就绪（需外置组件或配置）

- Batch 调度 → 需 **DolphinScheduler**
- Batch 集成 CDC → 轻量轮询，非 Debezium 管道
- K3s 测试 → 无 IRSA 时 Paimon 可用 `file://`/PVC，S3 需 MinIO 或直连 AWS

### 已知局限（产品级）

1. **Batch 调度**：K8s/EKS 默认不 bundled Dolphin。  
2. **Batch 集成 CDC**：非 Flink CDC / Debezium 一体化。  
3. **血缘**：基于 SQL 正则，复杂脚本可能不全。  
4. **质量执行**：非所有 JDBC 类型对等支持。  
5. **流 SQL**：复杂 DDL/多语句依赖 SqlRunner 与 Catalog 配置。  
6. **Postgres 元库**：K8s 清单默认 emptyDir，**生产须改 PVC**。

---

## 9. 推荐落地组合

| 目标 | 推荐路径 |
|------|----------|
| **5 分钟体验三产品** | `./start-platform.sh` |
| **流计算生产（AWS）** | EKS + [CDC_PAIMON_EKS.md](./CDC_PAIMON_EKS.md) + S3 制品 |
| **数据 API 中台** | EKS 最小栈（仅 GIDO）+ 外置 RDS PG |
| **批流一体** | EKS GIDO + 外置 Dolphin + EKS Stream |
| **本机 Operator 联调** | Kind + `apply-gido-stack.sh` |

---

## 10. 相关文档

| 文档 | 说明 |
|------|------|
| [PRODUCT_OVERVIEW.md](./PRODUCT_OVERVIEW.md) | 界面截图与快速体验 |
| [CDC_PAIMON_EKS.md](./CDC_PAIMON_EKS.md) | EKS CDC→Paimon + S3 |
| [FLINK_ARCHITECTURE.md](./FLINK_ARCHITECTURE.md) | Operator vs 遗留 Session |
| [k8s/README.md](../k8s/README.md) | Kind / K3s |
| [k8s/eks/README.md](../k8s/eks/README.md) | EKS 示例 YAML |
| [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md) | 部署索引 |
