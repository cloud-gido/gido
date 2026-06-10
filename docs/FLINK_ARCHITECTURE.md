# GIDO Stream · Flink 统一架构

## 概述

GIDO Stream 中台默认采用 **单一 Flink 部署路径**：通过 **Flink Kubernetes Operator** 创建 `FlinkDeployment`，作业容器使用 **统一运行时镜像** `gido-flink-runtime`（构建标签同 `gido-flink-sql-runner`）。

- Flink 版本：**2.0.1**（Operator `flinkVersion: v2_0`）
- SQL / JAR 均提交为 Operator Application 模式
- 镜像内预置 **Paimon**、**MySQL CDC**、**PostgreSQL CDC** 与 **sql-runner.jar**

遗留 **Session** / **K8s Application** 提交仅当环境变量 `GIDO_LEGACY_FLINK_SUBMIT=true` 时可用。

## 架构示意

```
浏览器 → GIDO Frontend → GIDO Backend
                              ↓
                    Flink Kubernetes Operator (namespace: flink)
                              ↓
              FlinkDeployment (gido-sql-* / gido-jar-*)
                              ↓
         Pod: gido-flink-runtime (Flink 2.0.1 + connectors + sql-runner)
```

## 统一运行时镜像

| 组件 | 路径 / 坐标 |
|------|-------------|
| 基座 | `apache/flink:2.0.1-java11` |
| SQL Runner | `/opt/flink/usrlib/sql-runner.jar` |
| Paimon | `org.apache.paimon:paimon-flink-2.0:1.3.2` → `/opt/flink/lib/` |
| MySQL CDC | `org.apache.flink:flink-sql-connector-mysql-cdc:3.5.0` |
| Postgres CDC | `org.apache.flink:flink-sql-connector-postgres-cdc:3.5.0` |

完整清单见 `k8s/flink-runtime/connectors.manifest`。

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
| Session | `GIDO_LEGACY_FLINK_SUBMIT=true` + `k8s/flink.yaml` Session 栈 | SQL Gateway / JM |
| K8s Application | 同上 + Gateway v4 | 已由 Operator 路径取代 |

`k8s/flink.yaml` 已标记 **DEPRECATED**，新环境请勿依赖。

## 一键部署

```bash
export KUBECONFIG=~/.kube/config-mac-orbstack   # 按实际集群修改
bash k8s/deploy-gido-k3s.sh
```

前置：已安装 Flink Kubernetes Operator CRD（见 `k8s/flink-operator-rbac.yaml`）。

验证：

1. `kubectl -n gido get pods` — backend / frontend Ready
2. `curl -s http://127.0.0.1:8001/api/streaming/flink-runtime`（经 port-forward 或 Ingress）
3. 前端「作业开发」应显示「统一运行时 · Flink Operator + gido-flink-runtime」
4. 创建 SQL 作业并提交，检查 `kubectl -n flink get flinkdeployments`
