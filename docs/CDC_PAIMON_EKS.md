# CDC → Paimon on AWS EKS（GIDO Stream）

面向 **GIDO Stream** 在 **AWS EKS** 上跑通 **MySQL binlog CDC → Flink 2.0.1 → Apache Paimon（S3 warehouse）** 的生产路径。

相关文档：

- 平台 EKS 总 SOP：[gido/docs/EKS-DEPLOYMENT-SOP.md](../gido/docs/EKS-DEPLOYMENT-SOP.md)
- K8s 清单与 Kind/K3s：[k8s/README.md](../k8s/README.md)
- EKS 示例 YAML：[k8s/eks/README.md](../k8s/eks/README.md)

---

## 1. 架构

```text
┌─────────────────────────────────────────────────────────────────────────┐
│  AWS EKS                                                                 │
│  ns: gido          gido-backend ──► 创建 FlinkDeployment CR             │
│                    gido-frontend    Stream Studio SQL 模板 / 提交          │
│  ns: flink         FlinkDeployment (JM/TM Pods, SA=flink + IRSA)        │
│  ns: flink-operator   Flink Kubernetes Operator 1.15                     │
└───────────────┬───────────────────────────────┬─────────────────────────┘
                │ MySQL binlog :3306              │ s3:// warehouse + checkpoint
                ▼                                 ▼
         RDS / Aurora MySQL                  S3 Bucket (IRSA)
```

**数据流**

1. Stream Studio 编写 SQL（或插入内置 CDC→Paimon 模板）
2. Backend 创建 `FlinkDeployment`，Pod 运行 `SqlRunner`（SQL 从 S3 或 ConfigMap 加载）
3. `mysql-cdc` 连接器读取 RDS binlog
4. Paimon catalog 将 changelog 写入 `s3://…/paimon-warehouse`
5. Flink checkpoint 写入 `s3://…/flink-checkpoints`（配置 `FLINK_OPERATOR_CHECKPOINT_DIR` 时）

**GIDO 默认提交模式**：`GIDO_FLINK_SUBMIT_MODE=operator`（不依赖 Session / SQL Gateway）。

---

## 2. 运行时镜像（必查）

统一镜像由 `k8s/flink-sql-runner/` 构建（`bash k8s/build-flink-runtime.sh`），包含：

| 组件 | 路径 / 版本 |
|------|-------------|
| Flink | 2.0.1-java11 |
| sql-runner | `/opt/flink/usrlib/sql-runner.jar` |
| Paimon | `paimon-flink-2.0` 1.3.2 → `/opt/flink/lib/` |
| MySQL CDC | `flink-sql-connector-mysql-cdc` 3.5.0 |
| Postgres CDC | 3.5.0（可选） |
| **S3 文件系统** | `flink-s3-fs-hadoop` 2.0.1 → `/opt/flink/plugins/s3-fs-hadoop/` |
| **Hadoop common** | `hadoop-common` 3.3.4 → `/opt/flink/lib/`（Paimon `CatalogContext` 须主 classpath 有 `Configuration`） |

**重要**：Paimon 文档说明若已通过 Flink S3 插件访问 S3，**不要**再添加 `paimon-s3-*.jar`，否则可能与 `flink-s3-fs-hadoop` 冲突。但 **仍须** 在 `/opt/flink/lib/` 提供 `hadoop-common`（插件 classloader 与 Paimon 主 classpath 隔离）。

EKS 上须将镜像 push 到 ECR，并在 `gido-backend-config` 设置：

```yaml
FLINK_OPERATOR_IMAGE: "<account>.dkr.ecr.<region>.amazonaws.com/gido-flink-runtime:stable"
FLINK_OPERATOR_SQL_RUNNER_JAR_URI: "local:///opt/flink/usrlib/sql-runner.jar"
```

---

## 3. 前置条件

### 3.1 AWS

- EKS 集群（建议 `eksctl create cluster … --with-oidc`）
- S3 bucket（例如 `s3://acme-gido-data/paimon-warehouse`）
- RDS / Aurora **MySQL 8.x**（或兼容 binlog 的 MySQL 5.7）
- （推荐）ECR 仓库：`gido-backend`、`gido-frontend`、`gido-flink-runtime`
- Flink Kubernetes Operator **1.15**（Flink `flinkVersion: v2_0`）

### 3.2 MySQL / RDS binlog

参数组建议：

| 参数 | 值 |
|------|-----|
| `binlog_format` | `ROW` |
| `binlog_row_image` | `FULL` |
| `log_bin` | `ON` |

CDC 专用用户（示例）：

```sql
CREATE USER 'cdc_user'@'%' IDENTIFIED BY 'strong-password';
GRANT SELECT, REPLICATION SLAVE, REPLICATION CLIENT ON shop.* TO 'cdc_user'@'%';
FLUSH PRIVILEGES;
```

**网络**：RDS 安全组入站允许 **EKS worker 节点安全组** → TCP **3306**（不是只放 backend Pod；Flink TM/JM 直连 MySQL）。

### 3.3 IRSA（S3 访问）

需要 **两个** ServiceAccount 的 IRSA（可共用 bucket、不同前缀）：

| SA | 命名空间 | 用途 |
|----|----------|------|
| `flink` | `flink` | Paimon warehouse、checkpoint、**读取** JAR/SQL 制品（`s3://` jarURI） |
| `gido-backend` | `gido` | **上传** Stream Studio 的 JAR/SQL 制品到 `FLINK_OPERATOR_JAR_S3_PREFIX` |

Flink 作业 Policy 需覆盖 warehouse、checkpoint 与 `gido-artifacts/*` 读权限；Backend Policy 需 `PutObject` / `HeadObject` 于制品前缀。

示例见 [k8s/eks/flink-s3-irsa.example.yaml](../k8s/eks/flink-s3-irsa.example.yaml) 与 [k8s/eks/gido-backend-s3-irsa.example.yaml](../k8s/eks/gido-backend-s3-irsa.example.yaml)。

```bash
eksctl create iamserviceaccount \
  --cluster=gido --namespace=flink --name=flink \
  --role-name=gido-flink-s3 \
  --attach-policy-arn=arn:aws:iam::ACCOUNT:policy/gido-flink-s3 \
  --approve --override-existing-serviceaccounts
```

使用 **静态 AK/SK** 时，可在 Paimon catalog 增加 `s3.access-key` / `s3.secret-key`（不推荐生产）。

### 3.4 GIDO Backend

- 部署在集群内，`serviceAccountName: gido-backend`，已有对 `flink` 命名空间 `FlinkDeployment` 的 RBAC（见 `k8s/gido.yaml`）
- ConfigMap 关键项（EKS 示例见 [gido-backend-eks-overrides.example.yaml](../k8s/eks/gido-backend-eks-overrides.example.yaml)）：

```yaml
GIDO_FLINK_SUBMIT_MODE: "operator"
PAIMON_WAREHOUSE_DEFAULT: "s3://YOUR-BUCKET/paimon-warehouse"
FLINK_OPERATOR_CHECKPOINT_DIR: "s3://YOUR-BUCKET/flink-checkpoints"
FLINK_OPERATOR_JAR_S3_PREFIX: "s3://YOUR-BUCKET/gido-artifacts"   # EKS 生产推荐
FLINK_OPERATOR_UPGRADE_MODE: "savepoint"   # 生产 CDC 建议 savepoint
FLINK_OPERATOR_NAMESPACE: "flink"
FLINK_OPERATOR_SERVICE_ACCOUNT: "flink"
FLINK_OPERATOR_JAR_HTTP_BASE: "http://backend.gido.svc.cluster.local:8001"  # HTTP 备用 / 调试
FLINK_OPERATOR_ARTIFACT_TOKEN: "<secret>"
```

配置 `FLINK_OPERATOR_JAR_S3_PREFIX` 后：

- 上传 JAR / 提交 SQL 时 Backend **自动上传** `artifact.jar` / `artifact.sql` 到 S3
- Operator `jarURI` 与 SqlRunner 脚本路径为 `s3://…`（无需 PVC 持久化制品）
- SQL CDC 作业默认 `sql_source=s3`（`SqlRunner` 经 `flink-s3-fs-hadoop` 读取）

---

## 4. 分步部署

### Step 1 — 安装 Operator 与 RBAC

```bash
# Operator 1.15（Helm，略；见 k8s/upgrade-flink-operator-1.15.sh）
kubectl create namespace flink --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f k8s/flink-operator-rbac.yaml
kubectl apply -f k8s/eks/flink-s3-irsa.example.yaml          # 替换 ACCOUNT_ID 后
kubectl apply -f k8s/eks/gido-backend-s3-irsa.example.yaml  # Backend 写制品
```

### Step 2 — 构建 Flink 运行时并 push ECR

```bash
export AWS_REGION=ap-northeast-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

aws ecr create-repository --repository-name gido-flink-runtime --region $AWS_REGION 2>/dev/null || true
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $REGISTRY

bash k8s/build-flink-runtime.sh
docker tag gido-flink-sql-runner:orbstack $REGISTRY/gido-flink-runtime:stable
docker push $REGISTRY/gido-flink-runtime:stable
```

### Step 3 — 部署 GIDO + EKS ConfigMap 覆盖

按 [EKS-DEPLOYMENT-SOP.md](../gido/docs/EKS-DEPLOYMENT-SOP.md) 部署 backend/frontend，并合并 [gido-backend-eks-overrides.example.yaml](../k8s/eks/gido-backend-eks-overrides.example.yaml) 中的项。

### Step 4 — Stream Studio 提交 CDC→Paimon

1. 打开 **GIDO Stream → 实时开发**
2. 新建 **SQL** 作业，提交模式 **Flink Operator**
3. 点击 **插入 CDC→Paimon 模板**（warehouse 来自 `PAIMON_WAREHOUSE_DEFAULT`）
4. 修改 MySQL `hostname` / 库表 / 密码
5. 资源规格：作业 `streaming_properties` 可设 `resource_tier: medium` 或 `operator_resources`
6. **发布 / 提交**

### Step 5 — 验证

```bash
kubectl -n flink get flinkdeployment
kubectl -n flink describe flinkdeployment gido-sql-<ws>-<jobId>
kubectl -n flink logs -l app=gido -c flink-main-container --tail=100
```

- Flink UI：开启 `FLINK_OPERATOR_UI_PROXY_ENABLED=true` 时经 GIDO 打开
- S3：控制台查看 `s3://YOUR-BUCKET/paimon-warehouse/ods.db/orders/` 等路径
- MySQL：在源表 INSERT/UPDATE，观察 Paimon 表文件变化

---

## 5. SQL 模板说明

内置模板（`StreamStudio.tsx`）结构：

1. `CREATE TABLE … WITH ('connector'='mysql-cdc', …)` — 源表
2. `CREATE CATALOG paimon WITH ('warehouse'='s3://…')`
3. `CREATE TABLE IF NOT EXISTS ods.orders …` — Paimon 表（primary key + changelog-producer）
4. `INSERT INTO ods.orders SELECT … FROM default_catalog.default_database.mysql_orders`

常用 CDC 选项：

| 选项 | 说明 |
|------|------|
| `server-id` | 集群内唯一，范围如 `5400-5404` |
| `scan.startup.mode` | `initial` 全量+增量；`latest-offset` 仅增量 |
| `server-time-zone` | 与 MySQL 会话时区一致 |

---

## 6. 作业级调优（streaming_properties）

Backend 支持 JSON 字段（Stream Studio 高级配置）：

```json
{
  "resource_tier": "medium",
  "operator_resources": {
    "upgradeMode": "savepoint",
    "flinkConfiguration": {
      "execution.checkpointing.interval": "60s"
    }
  }
}
```

`FLINK_OPERATOR_CHECKPOINT_DIR` 在 ConfigMap 层配置即可，会合并进 `FlinkDeployment.spec.flinkConfiguration`。

---

## 7. 故障排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `Unknown s3://` / 无法写 warehouse | 未装 S3 插件或 IRSA 无权限 | 重建 runtime 镜像；检查 IAM / SA 注解 |
| `Access denied` 连 MySQL | 安全组 / 用户 GRANT | 放行 EKS→RDS:3306；检查 REPLICATION 权限 |
| CDC 无增量 | binlog 未开或非 ROW | 改 RDS 参数组并重启 |
| Operator CR `FAILED` | SQL 语法 / 连接器版本 | `kubectl describe flinkdeployment`；查 TM 日志 |
| Pod `FailedScheduling` / untolerated taint | Flink 未调度到 bigdata 等节点池 | ConfigMap 设 `FLINK_OPERATOR_NODE_POOL=bigdata`；重建 backend 后重新提交作业 |
| 与 paimon-s3 冲突 | 同时存在两套 S3 实现 | 仅保留 `flink-s3-fs-hadoop` 插件 |
| `NoClassDefFoundError: org/apache/hadoop/conf/Configuration` | 镜像 `lib/` 缺 Hadoop | 重建 `gido-flink-runtime`（含 `hadoop-common` 单 jar），勿只装 S3 插件 |
| `NoSuchMethodError: commons-cli Option.builder` | hadoop 传递依赖污染 classpath | 仅保留 `hadoop-common-3.3.4.jar`，勿引入 `commons-cli-1.2` |

Flink CDC **3.5.0** 与 Flink **2.0.1** 为 GIDO 当前锁定组合；升级 Flink 2.2.x 时可评估 CDC **3.6.0-2.2**（见 `/api/streaming/flink-runtime` 的 `cdc_flink_compatibility_note`）。

---

## 8. 生产 Checklist

- [ ] EKS + OIDC + IRSA：`flink` SA（读 warehouse/checkpoint/制品）+ `gido-backend` SA（写制品）
- [ ] S3 bucket 生命周期 / 加密 / 禁止 public
- [ ] RDS binlog ROW + CDC 用户最小权限
- [ ] RDS SG ← EKS node SG :3306
- [ ] `gido-flink-runtime` ECR 镜像含 Paimon + mysql-cdc + **S3 插件**
- [ ] `PAIMON_WAREHOUSE_DEFAULT` / `FLINK_OPERATOR_CHECKPOINT_DIR` 指向 S3
- [ ] `FLINK_OPERATOR_UPGRADE_MODE=savepoint`（CDC 有状态作业）
- [ ] `FLINK_OPERATOR_JAR_S3_PREFIX` 已配置；Backend IRSA 可 PutObject
- [ ] Backend 在集群内 + RBAC + `FLINK_OPERATOR_ARTIFACT_TOKEN`
- [ ] Stream SQL 作业 smoke：模板提交 → RUNNING → S3 有数据
- [ ] 监控：Flink checkpoint 成功率、RDS 连接数、S3 请求错误率

---

## 9. 相关文件索引

| 路径 | 说明 |
|------|------|
| `k8s/flink-sql-runner/connectors-pom.xml` | 连接器与 S3 依赖 |
| `k8s/flink-sql-runner/Dockerfile` | S3 插件安装到 `plugins/s3-fs-hadoop` |
| `k8s/flink-runtime/connectors.manifest` | 镜像内组件清单 |
| `k8s/eks/*.example.yaml` | IRSA、Secret、ConfigMap 示例 |
| `gido/backend/app/services/artifact_s3.py` | JAR/SQL 制品 S3 上传 |
| `gido/backend/app/services/jar_artifact.py` | 本地 + S3 制品、`resolve_jar_uri_for_operator` |
| `gido/backend/app/services/flink_operator_submit.py` | FlinkDeployment 提交 |
| `gido/frontend/src/pages/StreamStudio.tsx` | CDC→Paimon SQL 模板 |
