# GIDO 生产部署环境变量详解（`gido-production.env`）

本文说明 [`gido-production.env.example`](gido-production.env.example) 中各变量的含义、写入 K8s 的位置，以及 [`apply-gido-production.sh`](apply-gido-production.sh) 的渲染规则。

**适用清单：** [`gido-production-external-pg.yaml`](gido-production-external-pg.yaml)（**不含集群内 PostgreSQL**，元库须外置）。

---

## 快速开始

```bash
cp k8s/gido-production.env.example k8s/gido-production.env
# 编辑 gido-production.env（勿提交 git）
bash k8s/apply-gido-production.sh
kubectl -n gido exec deploy/gido-backend -- python init_db.py   # 首次部署
kubectl -n gido port-forward svc/frontend 8080:80
```

指定 env 文件路径：

```bash
GIDO_PRODUCTION_ENV=/path/to/my.env bash k8s/apply-gido-production.sh
```

渲染结果默认写入 `/tmp/gido-production.rendered.yaml`（可用 `GIDO_RENDERED_MANIFEST` 覆盖）。

**前置：** Flink Kubernetes Operator **1.15** 已 Helm 安装（见 [`upgrade-flink-operator-1.15.sh`](upgrade-flink-operator-1.15.sh)）。

**EKS + RDS：** 使用 [`eks/gido-eks-external-pg.yaml`](eks/gido-eks-external-pg.yaml) 与 [`eks/apply-gido-eks.sh`](eks/apply-gido-eks.sh)，变量名不同，见 [EKS 部署 SOP](../gido/docs/EKS-DEPLOYMENT-SOP.md)。

---

## 一、GHCR CI 镜像

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `GIDO_GHCR_REPO` | 否 | `ghcr.io/cloud-gido/gido` | GHCR 仓库根路径 |
| `GIDO_CI_PROFILE` | 否 | `main` | CI 分支镜像布局：`main` / `dev` / `dev-1` |
| `GIDO_IMAGE_TAG` | 否 | `latest` | Backend / Frontend 镜像 tag |
| `GIDO_FLINK_RUNTIME_VERSION` | 否 | `2.2.1` | Flink 运行时版本号（dev-1 路径会用到） |
| `GIDO_FLINK_RUNTIME_TAG` | 否 | （空） | Flink 运行时 tag；**留空**时按 profile 自动推断 |
| `GIDO_FLINK_OPERATOR_FLINK_VERSION` | 否 | `v2_2` | Operator CRD 的 `flinkVersion`；Flink 2.2.x 填 **`v2_2`** |

### 镜像路径规则（脚本自动拼接）

| `GIDO_CI_PROFILE` | Backend | Frontend | Flink Runtime |
|-------------------|---------|----------|---------------|
| `main` | `{REPO}/gido-backend:{TAG}` | `{REPO}/gido-frontend:{TAG}` | `{REPO}/gido-flink-runtime:{FLINK_TAG}` |
| `dev` | 同上 | 同上 | 同上 |
| `dev-1` | `{REPO}/dev-1/gido-backend:{TAG}` | `{REPO}/dev-1/gido-frontend:{TAG}` | `{REPO}/dev-1/flink-runtime/{VERSION}:{FLINK_TAG}` |

### `FLINK_TAG` 自动规则（`GIDO_FLINK_RUNTIME_TAG` 为空时）

- `main` → 使用 `GIDO_FLINK_RUNTIME_VERSION`（如 `2.2.1`）
- `dev` / `dev-1` → 使用 `GIDO_IMAGE_TAG`（如 `dev-1`）

### 写入 K8s

| 目标 | 内容 |
|------|------|
| Deployment `gido-backend` / `gido-frontend` | `image: ...` |
| ConfigMap `gido-backend-config` | `FLINK_OPERATOR_IMAGE`、`FLINK_K8S_APPLICATION_IMAGE`、`FLINK_OPERATOR_FLINK_VERSION` |

---

## 二、外置 PostgreSQL（必填）

| 变量 | 必填 | 说明 |
|------|------|------|
| `GIDO_PG_HOST` | **是** | PG 主机名或 IP（RDS、云 PG、自建 PG） |
| `GIDO_PG_PORT` | **是** | 端口，通常 `5432` |
| `GIDO_PG_USER` | **是** | 数据库用户名 |
| `GIDO_PG_PASSWORD` | **是** | 数据库密码（明文，写入 Secret） |
| `GIDO_PG_DB` | **是** | 库名，通常 `gido` |

### 写入 K8s Secret `gido-secrets`

```yaml
INFRA_GIDO_DB_SERVICE_URL: "<GIDO_PG_HOST>:<GIDO_PG_PORT>"
INFRA_GIDO_DB_SERVICE_USER: "<GIDO_PG_USER>"
INFRA_GIDO_DB_SERVICE_PASSWORD: "<GIDO_PG_PASSWORD>"
INFRA_GIDO_DB_URL: "<GIDO_PG_DB>"
```

Backend 在四项齐全时 **优先** 于 `DATABASE_URL` 组装 `postgresql+psycopg2://...`（密码不进 URL，便于 Secret 注入）。详见 [`gido/backend/app/core/config.py`](../gido/backend/app/core/config.py) 中 `resolved_database_url`。

### initContainer

Backend Deployment 的 `wait-postgres` 使用 `GIDO_PG_HOST` + `GIDO_PG_PORT` 探测 PG 可达后再启动主容器。

### 注意事项

- 须提前 `CREATE DATABASE gido`（或账号具备建库权限；Backend 启动时会尝试自动建库）
- 网络：GIDO Pod 须能访问 `<host>:5432`（安全组 / 防火墙）
- 密码含特殊字符时，拆分变量比 URL 编码更简单
- 首次部署后执行：`kubectl -n gido exec deploy/gido-backend -- python init_db.py`

### PostgreSQL 15+：`permission denied for schema public`

若 `init_db.py` 报错 `InsufficientPrivilege` / `permission denied for schema public`，说明应用账号在 **`public` schema 无 CREATE 权限**（PG 15 起默认撤销）。

**用超级用户 / RDS master 连接目标库后执行：**

```sql
-- 将 gido 换成 INFRA_GIDO_DB_SERVICE_USER
GRANT CONNECT ON DATABASE gido TO gido;
GRANT USAGE, CREATE ON SCHEMA public TO gido;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gido;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO gido;
```

或一键脚本：[`postgres-external-init-grants.sql`](postgres-external-init-grants.sql)

```bash
psql -h YOUR_PG_HOST -U postgres -d gido -c "GRANT USAGE, CREATE ON SCHEMA public TO gido;"
kubectl -n gido exec deploy/gido-backend -- python init_db.py
```

**RDS 建议：** 建库时让 master 用户 `CREATE DATABASE gido OWNER gido;`，或建库后对 `gido` 用户执行上述 GRANT。

---

## 三、应用密钥（必填）

| 变量 | 必填 | 说明 |
|------|------|------|
| `GIDO_SECRET_KEY` | **是** | JWT 签名密钥；生产建议 **48+ 随机字符**，定期轮换 |
| `GIDO_ARTIFACT_TOKEN` | **是** | Flink Operator Pod **HTTP 拉取 JAR** 的鉴权 token（32+ 随机字符） |
| `GIDO_ADMIN_PASSWORD` | **是** | 首次 bootstrap 管理员 `admin` 的登录密码 |

### 写入 K8s Secret `gido-secrets`

| env 变量 | Secret 键 | Backend 配置项 |
|----------|-----------|----------------|
| `GIDO_SECRET_KEY` | `SECRET_KEY` | 登录 JWT |
| `GIDO_ARTIFACT_TOKEN` | `FLINK_OPERATOR_ARTIFACT_TOKEN` | 制品 HTTP 下载 |
| `GIDO_ADMIN_PASSWORD` | `GIDO_BOOTSTRAP_ADMIN_PASSWORD` | 首次 admin 密码 |

---

## 四、S3 制品库（可选）

**`GIDO_S3_BUCKET` 留空** → 不上传 S3，JAR 走 **PVC + HTTP**（`FLINK_OPERATOR_JAR_HTTP_BASE`）。

**填写 bucket 后**，脚本自动生成以下 ConfigMap 键：

| ConfigMap 键 | 生成规则（示例 bucket=`flink-on-devtest`，prefix=`gido-flink`） |
|--------------|----------------------------------------------------------------|
| `FLINK_OPERATOR_JAR_S3_PREFIX` | `s3://flink-on-devtest/gido-flink` |
| `PAIMON_WAREHOUSE_DEFAULT` | `s3a://flink-on-devtest/paimon-warehouse` |
| `FLINK_OPERATOR_CHECKPOINT_DIR` | `s3a://flink-on-devtest/flink/checkpoints` |
| `FLINK_OPERATOR_SAVEPOINT_DIR` | `s3a://flink-on-devtest/flink/savepoints` |

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `GIDO_S3_BUCKET` | 否 | （空） | S3 bucket 名（**不含** `s3://`） |
| `GIDO_S3_JAR_PREFIX` | 否 | `gido-flink` | JAR/SQL 制品在 bucket 下的 key 前缀 |
| `GIDO_S3_REGION` | 否 | （空） | AWS 区域 → ConfigMap `GIDO_ARTIFACT_S3_REGION` |
| `GIDO_S3_ENDPOINT_URL` | 否 | （空） | S3 API 地址 → ConfigMap `GIDO_ARTIFACT_S3_ENDPOINT_URL` |
| `GIDO_S3_AUTH_MODE` | 否 | `static` | `static`（AK/SK）或 `irsa`（EKS Pod IAM Role） |
| `GIDO_S3_USE_IRSA` | 否 | `false` | 与 auth mode 配合 → ConfigMap `FLINK_OPERATOR_S3_USE_IRSA` |

### 与 Flink `resource.*` 配置对照

| Flink 侧 | `gido-production.env` |
|----------|------------------------|
| `resource.aws.s3.bucket.name` | `GIDO_S3_BUCKET` |
| `resource.storage.upload.base.path` | `GIDO_S3_JAR_PREFIX` |
| `resource.aws.region` | `GIDO_S3_REGION` |
| `resource.aws.s3.endpoint` | `GIDO_S3_ENDPOINT_URL` |

### S3 AK/SK（本文件不包含）

`static` 模式须额外配置凭证，二选一：

1. 写入 Secret `gido-secrets`：`GIDO_S3_ACCESS_KEY_ID` / `GIDO_S3_SECRET_ACCESS_KEY`
2. 单独 Secret：[`s3-credentials-secret.example.yaml`](s3-credentials-secret.example.yaml)，并在 Backend Deployment `envFrom` 引用

`irsa` 模式（EKS）：为 `gido-backend` 与 `flink` ServiceAccount 绑定 IAM Role，见 [`eks/flink-s3-irsa.example.yaml`](eks/flink-s3-irsa.example.yaml)。

### 各 Operator 集群覆盖

平台 env 为 **默认值**。Stream Studio → **Operator 集群** Profile 可覆盖：

- JAR 制品 S3 前缀（`flink_operator_jar_s3_prefix`）
- S3 区域 / Endpoint（`flink_operator_s3_region` / `flink_operator_s3_endpoint_url`）
- AK/SK 或 IRSA（`flink_operator_s3_*`）

详见 [`gido/docs/OPERATOR_CLUSTER_PROFILE.md`](../gido/docs/OPERATOR_CLUSTER_PROFILE.md)。

---

## 五、Flink Web UI（方案 A：集群内 Backend 反向代理）

**适用：** [`gido-production-external-pg.yaml`](gido-production-external-pg.yaml) 将 `gido-backend` 与 Flink 作业部署在**同一 K8s 集群**。

浏览器 → GIDO Frontend → **gido-backend** → `{deployment_name}-rest.{namespace}.svc.cluster.local:8081`。

### ConfigMap `gido-backend-config` 键（清单默认已写入）

| ConfigMap 键 | 生产推荐值 | 说明 |
|--------------|------------|------|
| `FLINK_OPERATOR_UI_PROXY_ENABLED` | **`true`** | 经 GIDO `/api/streaming/jobs/{id}/flink-ui` 打开 UI |
| `FLINK_OPERATOR_JM_REST_TEMPLATE` | `http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081` | Backend 连 JM REST |
| `FLINK_OPERATOR_JAR_HTTP_BASE` | `http://backend.gido.svc.cluster.local:8001` | Operator 拉 JAR |
| `FLINK_OPERATOR_DEV_LOCAL` | `false` | 生产关闭 |
| `FLINK_OPERATOR_AUTO_UI_TUNNEL` | `false` | 仅 Backend 在集群外时开启 |
| `FLINK_OPERATOR_UI_URL_TEMPLATE` | （空） | 有 Ingress 时填 `https://{deployment_name}-flink.example.com` |
| `FLINK_OPERATOR_BROWSER_JM_BASE` | （空） | 可选 |
| `FLINK_K8S_REST_EXPOSED_TYPE` | `ClusterIP` | 方案 A 无需 LoadBalancer |

### `gido-production.env` 可选覆盖

| env 变量 | 写入 ConfigMap 键 |
|----------|------------------|
| `GIDO_FLINK_OPERATOR_UI_URL_TEMPLATE` | `FLINK_OPERATOR_UI_URL_TEMPLATE` |
| `GIDO_FLINK_OPERATOR_BROWSER_JM_BASE` | `FLINK_OPERATOR_BROWSER_JM_BASE` |

Pod 内环境变量键名为 **`FLINK_OPERATOR_*`**，不是 `GIDO_FLINK_OPERATOR_*`。

### 部署后校验

```bash
kubectl -n gido exec deploy/gido-backend -- sh -c 'env | grep -E "FLINK_OPERATOR_UI|FLINK_OPERATOR_JM_REST|FLINK_OPERATOR_JAR_HTTP"'
kubectl -n flink get svc | grep gido-jar
```

---

## 六、其他可选

| 变量 | 默认 | 说明 |
|------|------|------|
| `GIDO_STORAGE_CLASS` | （空） | PVC `gido-jar-artifacts` 的 StorageClass；空则删除该行，使用集群默认 |
| `GIDO_IMAGE_PULL_SECRET` | `ghcr-pull` | Backend / Frontend 拉私有镜像的 Secret 名；设 **`none`** 则去掉 `imagePullSecrets` |
| `GIDO_FLINK_IMAGE_PULL_SECRETS` | 同左 | Flink 作业 Pod 的 `imagePullSecrets`（ConfigMap `FLINK_OPERATOR_IMAGE_PULL_SECRETS`） |
| `GIDO_RENDERED_MANIFEST` | `/tmp/gido-production.rendered.yaml` | 渲染结果输出路径，便于审计 diff |

### 创建 GHCR pull secret 示例

```bash
kubectl create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=YOUR_GH_USER \
  --docker-password=YOUR_PAT \
  -n gido

kubectl create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=YOUR_GH_USER \
  --docker-password=YOUR_PAT \
  -n flink
```

---

## 七、完整生产示例

```bash
# --- 镜像 ---
GIDO_GHCR_REPO=ghcr.io/cloud-gido/gido
GIDO_CI_PROFILE=dev-1
GIDO_IMAGE_TAG=dev-1
GIDO_FLINK_RUNTIME_VERSION=2.2.1
GIDO_FLINK_OPERATOR_FLINK_VERSION=v2_2

# --- 外置 PG ---
GIDO_PG_HOST=pg.prod.internal
GIDO_PG_PORT=5432
GIDO_PG_USER=gido
GIDO_PG_PASSWORD='YourStrongPgPassword!'
GIDO_PG_DB=gido

# --- 密钥 ---
GIDO_SECRET_KEY='random-jwt-secret-at-least-48-chars-long'
GIDO_ARTIFACT_TOKEN='random-artifact-token-32-chars-min'
GIDO_ADMIN_PASSWORD='YourAdminLoginPassword!'

# --- S3（可选）---
GIDO_S3_BUCKET=flink-on-devtest
GIDO_S3_JAR_PREFIX=gido-flink
GIDO_S3_REGION=ap-southeast-1
GIDO_S3_ENDPOINT_URL=https://s3.ap-southeast-1.amazonaws.com
GIDO_S3_AUTH_MODE=static
GIDO_S3_USE_IRSA=false

# --- 可选 ---
GIDO_STORAGE_CLASS=gp3
GIDO_IMAGE_PULL_SECRET=ghcr-pull
```

---

## 八、不在本 env 文件中的配置

| 项 | 配置方式 |
|----|----------|
| S3 AK/SK | `gido-secrets` 或 [`s3-credentials-secret.example.yaml`](s3-credentials-secret.example.yaml) |
| EKS IRSA Role | `gido-backend` ServiceAccount annotation（见 EKS 清单） |
| Flink Operator 控制器 | Helm 单独安装 1.15 |
| Ingress / 对外域名 | 通用清单无 Ingress；EKS 见 [`eks/gido-eks-external-pg.yaml`](eks/gido-eks-external-pg.yaml) |
| 各集群 S3 差异 | UI → Operator 集群 Profile |
| DolphinScheduler | `DS_ENABLED` / `DS_URL` / `DS_TOKEN`（ConfigMap，默认关闭） |

---

## 九、相关文件

| 文件 | 用途 |
|------|------|
| [`gido-production.env.example`](gido-production.env.example) | env 模板 |
| [`apply-gido-production.sh`](apply-gido-production.sh) | 渲染 + apply |
| [`gido-production-external-pg.yaml`](gido-production-external-pg.yaml) | 生产 K8s 清单（外置 PG） |
| [`eks/gido-eks-external-pg.yaml`](eks/gido-eks-external-pg.yaml) | EKS + RDS + S3 + IRSA |
| [`gido/config/flink-operator.production.env.example`](../gido/config/flink-operator.production.env.example) | Backend `.env` 对照（非 K8s 渲染） |
