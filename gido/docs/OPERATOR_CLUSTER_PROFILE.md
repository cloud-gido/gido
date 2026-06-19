# Operator 集群（FlinkOperatorProfile）参数详解

面向：**实时开发 → Operator 集群** 新增/编辑表单，或调用 `POST/PUT /api/streaming/flink-operator-profiles` 时填写各字段。

---

## 配置如何生效

```text
平台环境变量（.env / K8s ConfigMap，Settings 默认）
  → 工作空间 Operator Profile（本页各字段，NULL/空 = 继承平台）
    → 作业级覆盖（作业开发页，非 Profile 表单内）
         · flink_operator_profile_id：选哪套集群
         · streaming_properties.operator_runtime_image：运行时镜像
         · streaming_properties.operator_flink_version：CRD 版本
         · flink_operator_submit_namespace：单次提交命名空间（API/库字段，一般 UI 自动写入）
```

列表与详情中的 **「生效配置 / effective」** = 上述合并后的最终结果，提交 `FlinkDeployment` 与拉 JM REST 时使用。

**创建校验**：`name` 必填；**`flink_operator_namespace` 与 `flink_operator_image` 至少填一项**（其余可留空以继承平台默认）。

---

## 基本信息

### `name`（名称）— 必填

| 项 | 说明 |
|----|------|
| **含义** | 工作空间内展示名，出现在作业开发「Operator 集群」下拉与运维概览中。 |
| **示例** | `prod-eks-flink`、`kind-dev`、`saas-flink-test` |
| **注意** | 仅 GIDO 元数据标识，不写入 K8s；同一工作空间内建议唯一、可读。 |

### `description`（说明）— 可选

| 项 | 说明 |
|----|------|
| **含义** | 备注：环境、负责人、集群 ID、变更记录等。 |
| **示例** | `生产 EKS us-east-1，bigdata 节点池，IRSA 已绑` |
| **默认** | 空 |

### `is_default`（设为默认）— 可选，默认 `false`

| 项 | 说明 |
|----|------|
| **含义** | 同一工作空间仅一条可为默认；新建作业若未选手动 Profile，后续扩展可默认指向该集群（当前作业仍以「平台默认」或显式选择为准）。 |
| **行为** | 设为 `true` 时，会将同工作空间其他 Profile 的 `is_default` 置为 `false`。 |
| **建议** | 生产主集群可设默认，测试集群不设。 |

### `is_enabled`（启用）— 可选，默认 `true`

| 项 | 说明 |
|----|------|
| **含义** | `false` 时不出现在下拉列表，且作业不能绑定该 Profile（绑定会 400）。 |
| **用途** | 临时下线某套集群配置，而不删除历史记录。 |

---

## 集群与提交

### `flink_operator_namespace`（提交命名空间）

| 项 | 说明 |
|----|------|
| **含义** | GIDO Backend 创建/更新 **`FlinkDeployment` CR 的 K8s 命名空间**（`metadata.namespace`）。Flink 作业 Pod（JM/TM）运行在此 ns。 |
| **与 Operator 的关系** | Flink Kubernetes **Operator 控制器**可在 `gido`、`flink-operator` 等 ns，但 **`watchNamespaces` 必须包含本字段** 才能 reconcile 作业 CR。 |
| **继承默认** | 空 → `FLINK_OPERATOR_NAMESPACE` → `FLINK_K8S_NAMESPACE` → `flink` |
| **对应 .env** | `GIDO_FLINK_OPERATOR_NAMESPACE` / `GIDO_FLINK_K8S_NAMESPACE` |
| **示例** | `flink` |
| **注意** | `imagePullSecrets`、RBAC ServiceAccount 须在此 ns 存在；私有镜像 Secret 建在**作业 ns**，不是 Operator 控制器 ns。 |

### `flink_k8s_context`（Kube Context）

| 项 | 说明 |
|----|------|
| **含义** | Backend 执行 `kubectl`/K8s Python 客户端时使用的 **kubeconfig context 名称**，用于连接**目标集群**。 |
| **何时必填** | Backend **不在**目标集群内、或一台 GIDO 管理多套 K8s 时必填。 |
| **继承默认** | 空 → `FLINK_K8S_CONTEXT` |
| **对应 .env** | `GIDO_FLINK_K8S_CONTEXT` |
| **示例** | `kind-gido`、`arn:aws:eks:us-east-1:123456789012:cluster/prod` |
| **注意** | 与 `flink_k8s_kubeconfig_path` 配合；集群内 Pod 且已挂载 in-cluster SA 时可留空。 |

### `flink_k8s_kubeconfig_path`（Kubeconfig 路径）

| 项 | 说明 |
|----|------|
| **含义** | **Backend 进程所在环境**内 kubeconfig 文件的绝对路径。 |
| **继承默认** | 空 → `FLINK_K8S_KUBECONFIG_PATH` |
| **对应 .env** | `GIDO_FLINK_K8S_KUBECONFIG_PATH` |
| **示例** | 容器内 `/tmp/kube-for-backend`；Kind 开发常由 entrypoint 从宿主机 `~/.kube/config` 写入 |
| **注意** | Docker Compose 本机 Kind：见 `gido/config/flink-operator.kind-local.env.example`；EKS 生产 Backend 在集群内通常不需要。 |

### `flink_k8s_cluster_domain`（集群 DNS 后缀）

| 项 | 说明 |
|----|------|
| **含义** | K8s 集群 Service FQDN 后缀，用于拼接 `{service}.{namespace}.svc.{domain}`。 |
| **继承默认** | 空 → `FLINK_K8S_CLUSTER_DOMAIN` → `cluster.local` |
| **对应 .env** | `GIDO_FLINK_K8S_CLUSTER_DOMAIN` |
| **示例** | `cluster.local`（默认）；部分自建集群可能是 `cluster.local` 以外值 |
| **使用处** | JM REST 模板解析、部分 K8s Application 遗留路径；标准 EKS/GKE 一般保持默认。 |

---

## 运行时

### `flink_operator_image`（运行时镜像）

| 项 | 说明 |
|----|------|
| **含义** | 写入 `FlinkDeployment.spec.image`，即 **Flink JM/TM 容器镜像**（须含 Flink 2.2.1 + 所需 connector，生产常用 `gido-flink-runtime`）。 |
| **继承默认** | 空 → `FLINK_OPERATOR_IMAGE` → `FLINK_K8S_APPLICATION_IMAGE` → `apache/flink:2.2.1-java11` |
| **对应 .env** | `GIDO_FLINK_OPERATOR_IMAGE` |
| **示例** | `ghcr.io/org/gido-flink-runtime:2.2.1`、`123456789.dkr.ecr.us-east-1.amazonaws.com/gido-flink-runtime:latest` |
| **注意** | SQL 作业依赖镜像内 `sql-runner.jar`；CDC/Paimon 依赖镜像内 connector。作业级可在「运行时镜像」再覆盖。 |

### `flink_operator_flink_version`（Operator CRD 版本）

| 项 | 说明 |
|----|------|
| **含义** | 写入 `FlinkDeployment.spec.flinkVersion`，须与集群已安装的 **FlinkDeployment CRD API 版本**一致。 |
| **继承默认** | 空 → `FLINK_OPERATOR_FLINK_VERSION` → `v2_2` |
| **对应 .env** | `GIDO_FLINK_OPERATOR_FLINK_VERSION` |
| **常用值** | `v2_2`（Flink **2.2.x**，GIDO 默认）；`v2_0`（2.0.x）；`v1_20`（1.20.x）；**`v1_17`（1.17.x，如 1.17.2）** |
| **自动推断** | Profile 只填镜像 `…:1.17.2-java11` 且未填本字段时，Backend 自动设为 `v1_17` |
| **示例配置** | 见 [../config/flink-operator.flink-1.17.env.example](../config/flink-operator.flink-1.17.env.example) |
| **注意** | Operator Pod 已是 1.15 但 CRD 未升级时，提交会 **422**；需 `kubectl apply` Operator 包内 `crds/`。CRD 版本与 Flink 小版本必须匹配文档。 |

### `flink_operator_service_account`（ServiceAccount）

| 项 | 说明 |
|----|------|
| **含义** | `FlinkDeployment` Pod 使用的 K8s **ServiceAccount 名称**（`podTemplate.spec.serviceAccountName`）。 |
| **继承默认** | 空 → `FLINK_OPERATOR_SERVICE_ACCOUNT` → `flink` |
| **对应 .env** | `GIDO_FLINK_OPERATOR_SERVICE_ACCOUNT` |
| **示例** | `flink` |
| **注意** | 须在该 Profile 的 **提交命名空间** 存在，且 RBAC 允许创建 Pod、读 ConfigMap 等；EKS IRSA 通过 SA annotation 绑 IAM Role（见 `k8s/eks/`）。 |

### `flink_operator_image_pull_secrets`（imagePullSecrets）

| 项 | 说明 |
|----|------|
| **含义** | 逗号或分号分隔的 **Secret 名称列表**，写入 `FlinkDeployment.spec.podTemplate.spec.imagePullSecrets`，供作业 Pod 拉私有镜像。 |
| **继承默认** | 空 → `FLINK_OPERATOR_IMAGE_PULL_SECRETS` |
| **对应 .env** | `GIDO_FLINK_OPERATOR_IMAGE_PULL_SECRETS` |
| **示例** | `ghcr-pull` 或 `ghcr-pull,ecr-pull` |
| **前置** | Secret 类型 `kubernetes.io/dockerconfigjson`，且建在 **`flink_operator_namespace` 同名 ns**；示例见 `k8s/flink-image-pull-secret.example.yaml`。 |

---

## 高级

### `flink_operator_jm_rest_template`（JM REST 模板）

| 项 | 说明 |
|----|------|
| **含义** | GIDO Backend **查询作业状态、取消作业、代理 Flink Web UI** 时访问 JobManager REST 的 URL 模板。 |
| **占位符** | `{deployment_name}`：FlinkDeployment 名（如 `gido-sql-w1-42`）；`{namespace}`：提交命名空间 |
| **继承默认** | 空 → `FLINK_OPERATOR_JM_REST_TEMPLATE` → `http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081` |
| **对应 .env** | `GIDO_FLINK_OPERATOR_JM_REST_TEMPLATE` |
| **生产建议** | Backend 与 Flink **同集群**时用集群 DNS（上式）；跨集群或本机 Kind 可能配合 NodePort / port-forward（见 `FLINK_OPERATOR_DEV_LOCAL` 等平台变量，**不在 Profile 表单**）。 |

### `flink_operator_checkpoint_dir`（Checkpoint 目录）

| 项 | 说明 |
|----|------|
| **含义** | 写入 Flink 配置 `state.checkpoints.dir` 及 savepoint 推导路径；启用 checkpoint 与有状态升级。 |
| **继承默认** | 空 → `FLINK_OPERATOR_CHECKPOINT_DIR`（平台级） |
| **对应 .env** | `GIDO_FLINK_OPERATOR_CHECKPOINT_DIR` |
| **示例** | `s3://my-bucket/flink-checkpoints`（EKS + IRSA）；`file:///opt/flink/checkpoints`（本地 PVC） |
| **注意** | S3 路径需 Flink Pod 可读写的凭证；**各 Operator 集群 Profile 可配置独立 AK/SK**（`flink_operator_s3_*`），未填时继承平台 `FLINK_OPERATOR_S3_*`。 |

### `flink_operator_s3_auth_mode` / AK/SK（S3 认证）

| 项 | 说明 |
|----|------|
| **含义** | 该 Operator 集群访问 S3（checkpoint / warehouse）的认证方式。 |
| **取值** | `static`（本集群 AK/SK，注入 Flink Pod `AWS_*` env）\| `irsa`（EKS Pod IRSA） |
| **继承默认** | 空 → 平台 `FLINK_OPERATOR_S3_AUTH_MODE` |
| **密钥** | `flink_operator_s3_access_key_id` / `flink_operator_s3_secret_access_key` / 可选 `session_token`；API 不回显 Secret |
| **示例** | static + `s3a://my-bucket/flink/checkpoints` + 集群专属 IAM User AK/SK |

### `flink_operator_jar_s3_prefix`（JAR/SQL 制品 S3 前缀）

| 项 | 说明 |
|----|------|
| **含义** | 该 Operator 集群的 JAR/SQL 制品库根路径；上传与 Operator `jarURI` 均使用此前缀。 |
| **继承默认** | 空 → 平台 `FLINK_OPERATOR_JAR_S3_PREFIX` / `GIDO_ARTIFACT_S3_PREFIX` |
| **对象路径** | `{prefix}/{job_id}/artifact.jar`（SQL 为 `artifact.sql`） |
| **示例** | `s3://prod-bucket/eks-a/jars` 与 `s3://test-bucket/kind-dev/jars` 分集群隔离 |

---

## 平台 .env 对照（Profile 未填时继承）

| Profile 字段 | 环境变量（仓库根 `.env` 常用 `GIDO_` 前缀） | Settings 字段 |
|--------------|-----------------------------------------------|---------------|
| 提交命名空间 | `GIDO_FLINK_OPERATOR_NAMESPACE` | `FLINK_OPERATOR_NAMESPACE` |
| 运行时镜像 | `GIDO_FLINK_OPERATOR_IMAGE` | `FLINK_OPERATOR_IMAGE` |
| CRD 版本 | `GIDO_FLINK_OPERATOR_FLINK_VERSION` | `FLINK_OPERATOR_FLINK_VERSION` |
| ServiceAccount | `GIDO_FLINK_OPERATOR_SERVICE_ACCOUNT` | `FLINK_OPERATOR_SERVICE_ACCOUNT` |
| Kube Context | `GIDO_FLINK_K8S_CONTEXT` | `FLINK_K8S_CONTEXT` |
| Kubeconfig | `GIDO_FLINK_K8S_KUBECONFIG_PATH` | `FLINK_K8S_KUBECONFIG_PATH` |
| 集群域名 | `GIDO_FLINK_K8S_CLUSTER_DOMAIN` | `FLINK_K8S_CLUSTER_DOMAIN` |
| JM REST 模板 | `GIDO_FLINK_OPERATOR_JM_REST_TEMPLATE` | `FLINK_OPERATOR_JM_REST_TEMPLATE` |
| Checkpoint | `GIDO_FLINK_OPERATOR_CHECKPOINT_DIR` | `FLINK_OPERATOR_CHECKPOINT_DIR` |
| imagePullSecrets | `GIDO_FLINK_OPERATOR_IMAGE_PULL_SECRETS` | `FLINK_OPERATOR_IMAGE_PULL_SECRETS` |

完整 Operator 相关平台项（**不能**在 Profile 中 per-集群配置，仅全局 `.env`）示例：

| 变量 | 作用 |
|------|------|
| `FLINK_OPERATOR_JAR_HTTP_BASE` | Operator Pod 拉 JAR 的 GIDO Backend 基址 |
| `FLINK_OPERATOR_JAR_S3_PREFIX` | JAR/SQL 制品 S3 前缀 |
| `FLINK_OPERATOR_ARTIFACT_TOKEN` | 制品 HTTP 拉取鉴权 |
| `FLINK_OPERATOR_UPGRADE_MODE` | `stateless` / `savepoint` 等 |
| `FLINK_OPERATOR_NODE_POOL` | 节点池 nodeSelector / tolerations |
| `FLINK_OPERATOR_DEV_LOCAL` / `AUTO_UI_TUNNEL` | 本机 Kind 开发 UI 隧道 |

见 `gido/config/flink-operator.production.env.example`、`gido/config/flink-operator.kind-local.env.example`。

---

## 作业级参数（不在 Profile 表单，常与 Profile 联用）

| 位置 | 字段 | 说明 |
|------|------|------|
| 作业开发 | Operator 集群下拉 | 对应 `flink_operator_profile_id` |
| 作业开发 | 运行时镜像 | `streaming_properties.operator_runtime_image` 或 `runtime_image` |
| 作业 JSON | `operator_flink_version` / `flink_version` | 覆盖 CRD 版本 |
| 库字段 | `flink_operator_submit_namespace` | 提交成功后写入的实际 ns，一般无需手填 |

---

## 配置示例

### 生产 EKS（Backend 同集群）

```json
{
  "workspace_id": 1,
  "name": "prod-eks",
  "is_default": true,
  "flink_operator_namespace": "flink",
  "flink_operator_image": "123456789.dkr.ecr.us-east-1.amazonaws.com/gido-flink-runtime:2.2.1",
  "flink_operator_flink_version": "v2_2",
  "flink_operator_service_account": "flink",
  "flink_operator_jm_rest_template": "http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081",
  "flink_operator_checkpoint_dir": "s3://my-bucket/flink-checkpoints",
  "flink_operator_s3_auth_mode": "static",
  "flink_operator_s3_access_key_id": "AKIA...",
  "flink_operator_image_pull_secrets": "ecr-pull"
}
```

### 本机 Kind（Backend 在 Docker，连 kind-gido）

```json
{
  "workspace_id": 1,
  "name": "kind-dev",
  "flink_operator_namespace": "flink",
  "flink_operator_image": "apache/flink:2.2.1-java11",
  "flink_k8s_context": "kind-gido",
  "flink_k8s_kubeconfig_path": "/tmp/kube-for-backend"
}
```

---

## 相关文档

- [../README.md](../README.md) — Stream 菜单与快速启动
- [../../docs/FLINK_ARCHITECTURE.md](../../docs/FLINK_ARCHITECTURE.md) — Operator 架构
- [../../docs/CDC_PAIMON_EKS.md](../../docs/CDC_PAIMON_EKS.md) — EKS 生产
- [../../k8s/README.md](../../k8s/README.md) — Kind / RBAC / CRD 升级
- [../config/flink-operator.production.env.example](../config/flink-operator.production.env.example)
