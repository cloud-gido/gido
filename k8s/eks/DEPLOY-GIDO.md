# GIDO + Flink Operator on EKS（小白部署清单）

**目标**：装好 GIDO 平台，能在 Web 里提交 SQL，由 **Flink Kubernetes Operator** 拉起 JM/TM，读写 **S3**（checkpoint + Paimon）。

---

## 最快路径（5 步）

| 步 | 做什么 | 命令/文件 |
|----|--------|-----------|
| 1 | 装 Flink Operator | 见下方第 1 步 |
| 2 | 建命名空间 + RBAC + IRSA | `flink-operator-rbac.yaml` + `flink-s3-irsa` + `gido-backend-s3-irsa` |
| 3 | 部署 GIDO | `bash k8s/eks/apply-gido-eks.sh` |
| 4 | 初始化数据库 | `kubectl -n gido exec deploy/gido-backend -- python init_db.py` |
| 5 | Web 提交 SQL 验证 | port-forward → 新建作业 → 看 `kubectl -n flink get flinkdeployment` |

镜像用 **GHCR**（push `dev` 分支后 CI 自动构建）：

- `ghcr.io/cloud-gido/gido/gido-backend:dev`
- `ghcr.io/cloud-gido/gido/gido-frontend:dev`
- `ghcr.io/cloud-gido/gido/gido-flink-runtime:dev` ← **Flink 作业用这个**

---

## 第 0 步：你需要准备什么

| 资源 | 说明 |
|------|------|
| EKS 集群 | 已开 OIDC（IRSA） |
| RDS PostgreSQL | 库名 `gido`，安全组放行 EKS 节点 → 5432 |
| S3 Bucket | checkpoint、SQL 制品、Paimon warehouse |
| GHCR 拉取权限 | 节点或 imagePullSecret 能拉 `ghcr.io/cloud-gido/gido/*` |
| 域名（可选） | 有 ALB Ingress Controller 时用；否则 port-forward |

---

## 第 1 步：装 Flink Kubernetes Operator

```bash
helm repo add flink-operator https://downloads.apache.org/flink/flink-kubernetes-operator-1.15.0/
helm install flink-kubernetes-operator flink-operator/flink-kubernetes-operator \
  --namespace flink-operator --create-namespace

kubectl -n flink-operator get pods   # 应 Running
```

---

## 第 2 步：命名空间 + RBAC + IRSA

```bash
kubectl create namespace gido --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace flink --dry-run=client -o yaml | kubectl apply -f -

# GIDO backend 在 flink 命名空间创建 FlinkDeployment 的权限
kubectl apply -f k8s/flink-operator-rbac.yaml

# 编辑 ACCOUNT_ID / bucket 后 apply：
kubectl apply -f k8s/eks/flink-s3-irsa.example.yaml
kubectl apply -f k8s/eks/gido-backend-s3-irsa.example.yaml
```

IAM Policy 至少：`ListBucket` + `GetObject/PutObject/DeleteObject` on `s3://<bucket>/flink/*` 和 `paimon-warehouse/*`。

---

## 第 3 步：一键部署 GIDO

克隆仓库后，在**仓库根目录**执行：

```bash
export GIDO_USE_GHCR=1
export GIDO_GHCR_REPO=ghcr.io/cloud-gido/gido
export GIDO_EKS_IMAGE_TAG=dev

export GIDO_EKS_S3_BUCKET=你的bucket名
export GIDO_EKS_RDS_HOST=xxx.rds.amazonaws.com
export GIDO_EKS_DB_USER=gido
export GIDO_EKS_DB_PASSWORD='你的密码'
export GIDO_EKS_DB_NAME=gido
export GIDO_EKS_SECRET_KEY='随机JWT密钥至少48字符'
export GIDO_EKS_ARTIFACT_TOKEN='随机制品token至少32字符'
export GIDO_EKS_BACKEND_IRSA=arn:aws:iam::账号:role/gido-backend-s3

# 可选：无 Ingress 可省略，默认 gido.local
# export GIDO_EKS_INGRESS_HOST=gido.example.com

bash k8s/eks/apply-gido-eks.sh
```

脚本会自动把 backend / frontend / **flink-runtime** 三个镜像指到 GHCR。

**首次部署**初始化元库：

```bash
kubectl -n gido exec deploy/gido-backend -- python init_db.py
```

---

## 第 4 步：ConfigMap 必检（Flink Operator 能跑）

部署后确认 `kubectl -n gido get cm gido-backend-config -o yaml` 含：

```yaml
GIDO_FLINK_SUBMIT_MODE: "operator"
FLINK_OPERATOR_NAMESPACE: "flink"
FLINK_OPERATOR_SERVICE_ACCOUNT: "flink"
FLINK_OPERATOR_IMAGE: "ghcr.io/cloud-gido/gido/gido-flink-runtime:dev"
FLINK_OPERATOR_SQL_RUNNER_JAR_URI: "local:///opt/flink/usrlib/sql-runner.jar"
FLINK_OPERATOR_JAR_S3_PREFIX: "s3://<bucket>/flink/job-jar"
FLINK_OPERATOR_CHECKPOINT_DIR: "s3a://<bucket>/flink/checkpoints"
FLINK_OPERATOR_SAVEPOINT_DIR: "s3a://<bucket>/flink/savepoints"
FLINK_OPERATOR_S3_USE_IRSA: "true"
FLINK_OPERATOR_JAR_HTTP_BASE: "http://backend.gido.svc.cluster.local:8001"
FLINK_OPERATOR_NODE_POOL: "bigdata"   # 多节点池 + taint 时必填
```

**注意**：`JAR_HTTP_BASE` 里的 namespace 必须和 GIDO backend **实际所在 namespace** 一致（见下方「已有 bigdata 命名空间」）。

---

## 第 5 步：打开 GIDO

```bash
kubectl -n gido port-forward svc/frontend 8080:80
```

浏览器打开 http://127.0.0.1:8080 ，默认 **admin / admin123**（登录后请改密）。

---

## 第 6 步：提交 SQL 验证 Flink Operator

**作业开发 → 新建 SQL 作业**，粘贴：

```sql
CREATE CATALOG paimon_catalog WITH (
  'type' = 'paimon',
  'warehouse' = 's3://你的bucket/flink/paimon-warehouse'
);
USE CATALOG paimon_catalog;
CREATE DATABASE IF NOT EXISTS gido_smoke;
SHOW DATABASES;
```

提交后在 **作业运维** 看状态，或：

```bash
kubectl -n flink get flinkdeployment
kubectl -n flink logs deploy/<deployment名>-jobmanager --tail=100
kubectl -n flink exec deploy/<deployment名>-jobmanager -- \
  ls /opt/flink/lib/ | grep hadoop-common
```

看到 `hadoop-common-3.3.4.jar` 且没有 `NoClassDefFoundError: Configuration` → runtime 正确。

---

## 已有 `bigdata` 命名空间（生产补丁）

若 GIDO 已部署在 **`bigdata`**（不是模板默认的 `gido`），**不要**整份重 apply 模板，只需 patch ConfigMap + 换镜像 + 对齐 RBAC：

```bash
# 1. 换最新 flink-runtime（改完须新建作业，旧 FlinkDeployment 不会自动换镜像）
kubectl -n bigdata patch configmap gido-backend-config --type merge -p '{
  "data": {
    "FLINK_OPERATOR_IMAGE": "ghcr.io/cloud-gido/gido/gido-flink-runtime:dev",
    "FLINK_K8S_APPLICATION_IMAGE": "ghcr.io/cloud-gido/gido/gido-flink-runtime:dev",
    "FLINK_OPERATOR_NAMESPACE": "bigdata",
    "FLINK_OPERATOR_SERVICE_ACCOUNT": "flink-service-account-prod",
    "FLINK_OPERATOR_JAR_HTTP_BASE": "http://backend.bigdata.svc.cluster.local:8001",
    "FLINK_OPERATOR_S3_USE_IRSA": "true",
    "FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER": "com.amazonaws.auth.WebIdentityTokenCredentialsProvider"
  }
}'

# 2. 重启 backend 使配置生效
kubectl -n bigdata rollout restart deploy/gido-backend

# 3. RBAC：RoleBinding 的 subject 须在 bigdata，且 Role 在 FLINK_OPERATOR_NAMESPACE
#    检查 k8s/flink-operator-rbac.yaml 或手工 RoleBinding subject.namespace=bigdata
```

然后 **新建一条 SQL 作业** 提交（不要复用旧的 FlinkDeployment）。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| `NoClassDefFoundError: Configuration` | 用最新 `gido-flink-runtime:dev`，**新建作业**重提 |
| S3 AccessDenied | 检查 flink SA 的 IRSA + IAM 对 bucket 的权限 |
| Pod Pending | 设 `FLINK_OPERATOR_NODE_POOL=bigdata` + 对应 tolerations |
| JM REST DNS 失败 | 作业已崩溃，看 jobmanager logs |
| JAR 拉取 404 | `FLINK_OPERATOR_JAR_HTTP_BASE` namespace 写错 |
| 镜像是否合格 | `bash k8s/flink-sql-runner/verify-image.sh ghcr.io/cloud-gido/gido/gido-flink-runtime:dev` |

---

## 三个镜像（只记这个）

| 镜像 | 用途 |
|------|------|
| `gido-backend` | GIDO API，提交 FlinkDeployment |
| `gido-frontend` | Web UI |
| `gido-flink-runtime` | **Flink JM/TM + Paimon + S3 + sql-runner** |

push 到 `dev` 后 CI 自动构建。更新 runtime 后：改 ConfigMap 镜像 tag → restart backend → **新建作业**。
