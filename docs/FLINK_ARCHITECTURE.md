# GIDO Stream · Flink 统一架构

## 概述

GIDO Stream 中台默认采用 **单一 Flink 部署路径**：通过 **Flink Kubernetes Operator** 创建 `FlinkDeployment`，作业容器使用 **统一运行时镜像** `gido-flink-runtime`。

GitHub Actions（`.github/workflows/ci.yml` → `docker-flink-runtime`）构建 `k8s/flink-sql-runner/Dockerfile` 并推送 **`gido-flink-runtime`** 至 GHCR（`FLINK_OPERATOR_IMAGE`）。`sql-runner` 指镜像内的 JAR（`local:///opt/flink/usrlib/sql-runner.jar`），不是独立容器镜像。详见 `k8s/flink-sql-runner/README.md`。

- Flink 版本：**2.0.1**（Operator `flinkVersion: v2_0`）
- SQL / JAR 均提交为 Operator Application 模式
- 镜像内预置 **Paimon**、**MySQL CDC**、**PostgreSQL CDC** 与 **sql-runner.jar**

遗留 **Session** / **K8s Application** 提交仅当环境变量 `GIDO_LEGACY_FLINK_SUBMIT=true` 时可用。

答疑（CR / Operator / JM·TM / 停止与 checkpoint）：见 [FLINK_OPERATOR_FAQ.md](./FLINK_OPERATOR_FAQ.md)。  
计划停止超时（Checkpoint 正常但 Savepoint 失败）专项 QA：见 [FAQ §8](./FLINK_OPERATOR_FAQ.md#8-qa计划停止-savepoint-一直超时checkpoint-却正常)。

## 架构示意

```
作业开发：编辑 / 保存版本 / 提交不可变发布版本
                              ↓
作业运维：部署 / Savepoint 停止 / 选择恢复点重启 / 资源调整
                              ↓
                    GIDO Backend + 状态与操作审计
                              ↓
                    Flink Kubernetes Operator
                              ↓
              FlinkDeployment (gido-sql-* / gido-jar-*)
                              ↓
         Pod: gido-flink-runtime (Flink 2.0.1 + connectors + sql-runner)
```

开发面与运行面分离：

| 区域 | 职责 |
|------|------|
| 作业开发 | SQL/JAR、依赖、默认参数、草稿与版本历史、提交发布 |
| 作业运维 | 首次部署、Savepoint 停止、恢复/重启、运行资源、诊断、Flink UI、恢复点与操作历史 |
| 数据管道 | 配置化 Kafka→Paimon、Schema Contract、预检、生成物与发布 |
| 资源管理 | JAR、连接器、依赖文件；与作业运维同级，位于其下方 |

“提交”只产生不可变发布版本，不直接创建 FlinkDeployment。只有作业运维的“部署/重启”会改变集群状态。

## 统一运行时镜像

| 组件 | 路径 / 坐标 |
|------|-------------|
| 基座 | `apache/flink:2.0.1-java11` |
| SQL Runner | `/opt/flink/usrlib/sql-runner.jar` |
| Paimon | `org.apache.paimon:paimon-flink-2.0:1.3.2` → `/opt/flink/lib/` |
| Kafka SQL | `org.apache.flink:flink-sql-connector-kafka:4.0.1-2.0` |
| MySQL CDC | `org.apache.flink:flink-sql-connector-mysql-cdc:3.5.0` |
| Postgres CDC | `org.apache.flink:flink-sql-connector-postgres-cdc:3.5.0` |

完整清单见 `k8s/flink-runtime/connectors.manifest`。

## 声明式数据管道

GIDO Stream 数据管道不是第二套运行引擎，而是现有 Stream 生命周期之上的声明式编译层：

```
PipelineSpec
    ↓ 连接、Schema、容量和兼容性预检
确定性编译器
    ├─ JSON / 固定 Contract CDC / 轻转换 → Flink SQL（当前）
    ├─ CDC 自动演进 → Paimon Kafka CDC Action Runner（后续）
    └─ 原始字节 DLQ / 复杂转换 → 受控 DataStream Runner（后续）
    ↓
不可变 StreamingJobRelease
    ↓ 审批
FlinkDeployment + Savepoint 生命周期
```

核心约束：

- 普通 Kafka JSON 与 Debezium/Canal CDC 分开建模，只有 CDC Envelope 才表示更新和删除语义；Avro/Protobuf 执行后端在对应 Format JAR 纳入运行时后开放。
- 同一份 `PipelineSpec + compiler_version` 必须产生相同生成物与 hash；历史 Release 不在部署时重新编译。
- Pipeline 只引用连接与 Secret ID；生成 SQL 使用 `${env:...}` 占位符，部署时由独立 Kubernetes Secret 注入，Release、SQL 制品、ConfigMap、日志和接口响应均不得包含明文凭证。
- Kafka Topic、Confluent-compatible Schema Registry subjects 与 S3 Paimon warehouse 提供只读发现；生产 Schema 仍以已批准 Contract 为准。
- 首次建表仅允许在连接配置的 namespace 白名单内；现有表的删列、改名、主键或分区变化必须阻断并显式审批。当前 SQL 后端不静默执行 `ALTER TABLE`。
- 生产默认独立 FlinkDeployment。共享 DeploymentGroup 仅适用于同安全域、同运行时和兼容 checkpoint 策略的低 SLA 任务，且不能静默重组运行中管道。
- SQL Gateway 仅用于交互校验；生产生命周期仍统一由 Flink Kubernetes Operator 管理。

构建：

```bash
source k8s/lib/flink-sql-runner-image.sh
gido_flink_sql_runner_build linux/amd64 gido-flink-sql-runner:latest /path/to/gido
```

## 后端配置

| 变量 | 说明 |
|------|------|
| `GIDO_FLINK_SUBMIT_MODE` | 默认 `operator` |
| `GIDO_LEGACY_FLINK_SUBMIT` | `true` 时允许 Session/Application |
| `PAIMON_WAREHOUSE_DEFAULT` | 默认 Paimon warehouse（如 `s3://...`） |
| `FLINK_OPERATOR_IMAGE` | 指向统一运行时镜像 |
| `FLINK_OPERATOR_NAMESPACE` | Operator 部署命名空间（通常 `flink`） |
| `FLINK_OPERATOR_CHECKPOINT_DIR` | 可选默认 checkpoint 目录 |
| `FLINK_OPERATOR_SAVEPOINT_DIR` | 计划停止/恢复使用的持久 savepoint 目录 |
| `FLINK_OPERATOR_UPGRADE_MODE` | 作业升级默认模式；计划停止由平台显式使用 `savepoint` |

只读 API：`GET /api/streaming/flink-runtime`

## CDC → Paimon 示例 SQL

将 `__PAIMON_WAREHOUSE__` 替换为 `PAIMON_WAREHOUSE_DEFAULT` 或作业级路径；MySQL 连接信息按环境修改。

```sql
-- MySQL CDC 源表
CREATE TABLE mysql_orders (
  order_id BIGINT,
  user_id BIGINT,
  amount DECIMAL(10, 2),
  updated_at TIMESTAMP(3),
  PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
  'connector' = 'mysql-cdc',
  'hostname' = 'mysql.example.svc',
  'port' = '3306',
  'username' = 'cdc_user',
  'password' = '***',
  'database-name' = 'shop',
  'table-name' = 'orders'
);

-- Paimon 目标表（默认 warehouse）
CREATE CATALOG paimon WITH (
  'type' = 'paimon',
  'warehouse' = '__PAIMON_WAREHOUSE__'
);

USE CATALOG paimon;

CREATE TABLE IF NOT EXISTS ods.orders (
  order_id BIGINT,
  user_id BIGINT,
  amount DECIMAL(10, 2),
  updated_at TIMESTAMP(3),
  PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

INSERT INTO ods.orders
SELECT order_id, user_id, amount, updated_at FROM default_catalog.default_database.mysql_orders;
```

## Flink CDC 与 Flink 2.0.1 说明

Flink CDC **3.6+** 在 Maven 上为 `3.6.0-1.20` / `3.6.0-2.2`（无裸 `3.6.0`）。GIDO **Flink 2.0.1** 预置 **CDC 3.5.0** 以便构建与联调；**生产 CDC 链路请验证**，或升级 Flink 至 **2.2.x** 后改用 `3.6.0-2.2`。

## Paimon Warehouse（开发可选）

本地文件 warehouse 可使用 PVC：

```bash
kubectl apply -f k8s/paimon-warehouse-pvc.yaml
# warehouse: file:///opt/flink/paimon-warehouse（须在 FlinkDeployment podTemplate 挂载，见 PVC 文件注释）
```

## 遗留模式

| 模式 | 条件 | 说明 |
|------|------|------|
| Session | `GIDO_LEGACY_FLINK_SUBMIT=true` + `k8s/legacy/flink.yaml` Session 栈 | SQL Gateway / JM |
| K8s Application | 同上 + Gateway v4 | 已由 Operator 路径取代 |

`k8s/legacy/flink.yaml` 已标记 **DEPRECATED**，新环境请勿依赖。

## 一键部署

```bash
export KUBECONFIG=~/.kube/config-mac-orbstack   # 按实际集群修改
bash k8s/deploy-gido-k3s.sh
```

前置：已安装 Flink Kubernetes Operator CRD（见 `k8s/flink-operator-rbac.yaml`）。

验证：

1. `kubectl -n gido get pods` — backend / frontend Ready
2. `curl -s http://127.0.0.1:8001/api/streaming/flink-runtime`（经 port-forward 或 Ingress）
3. 前端「作业开发」提交版本后，作业运维应显示“待部署”
4. 在作业运维部署 SQL 作业，检查 `kubectl -n <FLINK_OPERATOR_NAMESPACE> get flinkdeployments`
5. 执行默认停止，确认 CR 进入 `suspended` 且恢复点历史出现成功 Savepoint
6. 从该 Savepoint 重启，确认状态连续且操作审计完整
