# GIDO Kubernetes 部署指南

本目录提供 **GIDO 最小 K8s 栈**（PostgreSQL + backend + frontend）及 **Flink Kubernetes Operator JAR 生产路径** 的脚本与清单。

| 场景 | 入口 |
|------|------|
| **本机 Kind 开发**（推荐） | 本文 §2 |
| **局域网 K3s / OrbStack** | 本文 §5 |
| Docker Compose 单机 | [gido/docs/DEPLOYMENT_SOP.md](../gido/docs/DEPLOYMENT_SOP.md) |
| AWS EKS | [gido/docs/EKS-DEPLOYMENT-SOP.md](../gido/docs/EKS-DEPLOYMENT-SOP.md) · CDC→Paimon：[docs/CDC_PAIMON_EKS.md](../docs/CDC_PAIMON_EKS.md) |

### 统一技术栈（当前默认）

| 组件 | 版本 |
|------|------|
| Flink Kubernetes Operator | **1.15.0**（升级：`k8s/upgrade-flink-operator-1.15.sh`；Chart 包名 `*-helm.tgz`） |
| Flink 运行时（Session + Operator JAR） | **2.0.1**（`apache/flink:2.0.1-java11`） |
| FlinkDeployment `flinkVersion` | **`v2_0`** |
| 示例 JAR（flink-wordcount-demo） | Maven `flink.version=2.0.1` |

---

## 1. 架构概览

```text
┌─────────────────────────────────────────────────────────────┐
│  Kind 集群 gido（本机一套，勿重复创建）                        │
├─────────────────────────────────────────────────────────────┤
│  ns: gido          postgres + gido-backend + gido-frontend  │
│  ns: flink         FlinkDeployment 作业（Operator 创建）     │
│  ns: flink-operator  Flink Kubernetes Operator                │
│  ns: headlamp      （可选）K8s 管理 UI                       │
└─────────────────────────────────────────────────────────────┘
         ▲
         │ kubectl apply + 镜像导入
         │
  apply-gido-stack.sh
```

**实时 JAR 路径**：上传 JAR → GIDO backend 调 Operator API → 创建 `FlinkDeployment`。  
**不含**：DolphinScheduler、Flink Session/SQL Gateway（可选见 `legacy/flink.yaml`）。

**Flink UI**：开启 `FLINK_OPERATOR_UI_PROXY_ENABLED` 后，浏览器经 GIDO 反向代理打开作业 UI，**无需**再 `port-forward` JobManager；只需转发 frontend `8080`。

---

## 2. 本机 Kind 部署

### 2.1 前置条件

- Docker、kubectl、kind
- 已安装 **Flink Kubernetes Operator**（CRD `flinkdeployments.flink.apache.org`）
- kubeconfig 指向 Kind，勿误用 OrbStack 默认配置（见 §2.2）

### 2.2 kubeconfig（重要）

本机通常只有 **一套 Kind 集群 `gido`**。若 `~/.zshrc` 里默认 `KUBECONFIG=~/.kube/config-mac-orbstack`，部署 Kind 时必须显式指定：

```bash
export KUBECONFIG=~/.kube/kind-gido.yaml
# 或单次前缀：KUBECONFIG=~/.kube/kind-gido.yaml kubectl ...
```

导出 Kind kubeconfig（首次）：

```bash
kind export kubeconfig --name gido --kubeconfig ~/.kube/kind-gido.yaml
kubectl config use-context kind-gido
```

### 2.3 首次：创建 Kind 集群

```bash
bash k8s/create-kind-gido.sh
# 或：KIND_CLUSTER_NAME=gido kind create cluster --config k8s/kind-gido-config.yaml
```

`kind-gido-config.yaml` 已写入 DaoCloud containerd 镜像加速；**第三方镜像仍建议**走 `kind-load-mirror-images.sh` 预导入（与节点同架构，Mac M 为 **linux/arm64**）。

### 2.4 一键部署 GIDO

```bash
KUBECONFIG=~/.kube/kind-gido.yaml \
KIND_CLUSTER_NAME=gido \
bash k8s/apply-gido-stack.sh
```

**脚本做了什么：**

| 步骤 | 说明 |
|------|------|
| `kind_image_build` | buildx 构建 **单架构** GIDO 镜像（Mac M 默认 `linux/arm64`，Intel Mac 为 `linux/amd64`） |
| `kind-load-mirror-images.sh` | 压平 postgres/busybox 等第三方镜像并 `ctr import` 进 Kind 节点 |
| `kubectl apply` | `gido.yaml`（替换 `__BACKEND_IMAGE__` / `__FRONTEND_IMAGE__`） |
| RBAC | 若存在 `flink` 命名空间，自动 apply `flink-operator-rbac.yaml` |

**常用环境变量：**

| 变量 | 默认 | 说明 |
|------|------|------|
| `GIDO_SKIP_BUILD=1` | — | 跳过构建，用已有本地镜像 |
| `GIDO_KIND_LOAD=0` | 自动 | 强制不导入 Kind（非 Kind 集群时用） |
| `GIDO_KIND_LOAD=1` | 自动 | 强制导入 Kind |
| `GIDO_APPLY_FLINK=1` | — | 额外部署 Session Flink（`legacy/flink.yaml`，一般不需要） |
| `GIDO_APPLY_INGRESS=1` | — | 部署 `gido-ingress.yaml` |
| `GIDO_BUILD_PLATFORM` | 自动检测 | 强制覆盖镜像平台（`linux/arm64` / `linux/amd64`） |
| `KIND_PLATFORM` | 同自动检测 | `kind-load-mirror-images.sh` 压平架构 |

仅更新清单、不重建镜像：

```bash
KUBECONFIG=~/.kube/kind-gido.yaml GIDO_SKIP_BUILD=1 bash k8s/apply-gido-stack.sh
```

### 2.5 访问 GIDO

```bash
KUBECONFIG=~/.kube/kind-gido.yaml kubectl port-forward -n gido svc/frontend 8080:80
```

浏览器：**http://127.0.0.1:8080**  
默认账号：`admin` / `admin123`（生产请改 Secret）

**注意**：frontend Pod 重启后 port-forward 会断开（`lost connection to pod`），需重新执行上述命令。

### 2.6 镜像平台自动检测

构建脚本通过 `gido_detect_build_platform` 选择 **单架构** 镜像（优先级）：

1. 环境变量 **`GIDO_BUILD_PLATFORM`**（显式指定时）
2. **`kubectl` 集群首节点** `status.nodeInfo.architecture`（部署到 K3s/Kind 时与节点一致）
3. **本机 CPU**（`uname -m`，无 kubeconfig 时）

日志会打印来源（如 `kubectl 集群节点` / `本机 CPU (arm64)`）。勿裸 `docker build`，请用 `bash k8s/build-flink-runtime.sh` 或 `deploy-gido-k3s.sh`。

Mac 上 `docker pull` 常得到错误架构或 OCI manifest index，导致 `no match for platform` / `CreateContainerError`。

治理逻辑在 `k8s/lib/kind-image.sh`：

- GIDO 自研镜像：`kind_image_build` 单架构构建
- 第三方镜像：`kind_image_pull_flatten` 经 buildx 从 **docker.io 上游**压平为节点架构再导入

单独预导入基础镜像：

```bash
KUBECONFIG=~/.kube/kind-gido.yaml KIND_CLUSTER_NAME=gido bash k8s/kind-load-mirror-images.sh
```

---

## 3. 关停与重启

### 3.1 只停网页访问

结束 `kubectl port-forward` 进程即可；集群内 Pod 仍在运行。

### 3.2 停掉 GIDO 应用（保留 Kind 集群）

**推荐**：本机只有一套 Kind，日常「关掉 GIDO」用这个即可。

```bash
KUBECONFIG=~/.kube/kind-gido.yaml kubectl delete namespace gido
```

影响范围：仅 `gido` 命名空间（postgres、backend、frontend、registry 等）。  
**不影响**：Kind 集群本身、`flink` / `flink-operator` / `headlamp` 等其他命名空间。

可选：只缩容不删命名空间：

```bash
KUBECONFIG=~/.kube/kind-gido.yaml kubectl scale deployment -n gido --all --replicas=0
```

顺带删除 Flink 作业：

```bash
KUBECONFIG=~/.kube/kind-gido.yaml kubectl delete flinkdeployment -n flink --all
```

### 3.3 拆掉整个 Kind 集群（慎用）

```bash
kind delete cluster --name gido
```

| 操作 | `kubectl delete ns gido` | `kind delete cluster --name gido` |
|------|--------------------------|-----------------------------------|
| 范围 | 仅 `gido` 命名空间 | 整个 Kind 集群 |
| Kind 还在吗 | ✅ 在 | ❌ 没了 |
| Flink Operator / Headlamp | ✅ 保留 | ❌ 一并删除 |
| 重建成本 | 再跑 `apply-gido-stack.sh` | 先 `create-kind-gido.sh`，再部署一切 |

**结论**：本机维护一套 Kind 即可；关停 GIDO 用 `delete ns gido`，不要用 `kind delete cluster`，除非整套本地 K8s 环境都不要了。

### 3.4 重新部署 GIDO

须**手动**执行（勿期待集群或 IDE 自动拉起）：

```bash
KUBECONFIG=~/.kube/kind-gido.yaml KIND_CLUSTER_NAME=gido bash k8s/apply-gido-stack.sh
```

---

## 4. Flink Operator 相关

### 4.1 前置 RBAC

```bash
KUBECONFIG=~/.kube/kind-gido.yaml kubectl apply -f k8s/flink-operator-rbac.yaml
```

### 4.2 配置要点（`gido.yaml` ConfigMap）

| 项 | 说明 |
|----|------|
| `FLINK_OPERATOR_UI_PROXY_ENABLED=true` | 浏览器经 GIDO 打开 Flink UI |
| `FLINK_OPERATOR_JAR_HTTP_BASE` | Operator 从 backend 拉 JAR 的地址 |
| `JAR_ARTIFACT_DIR` | JAR 存储目录（PVC `gido-jar-artifacts` → `/data/jar-artifacts`） |

环境变量模板：`gido/config/flink-operator.kind-local.env.example`、`flink-operator.production.env.example`。

### 4.3 提交 JAR 流程

1. GIDO Stream 界面上传 JAR  
2. 提交作业 → backend 创建 `FlinkDeployment`  
3. 点击「K8s 作业 UI」→ 经 GIDO 代理打开 Flink Dashboard（无需 JM port-forward）

---

## 5. 局域网 K3s / OrbStack 部署

适用于局域网节点（如 OrbStack K3s，`192.168.x.x`）；Mac M 上与 Kind **同为 arm64**。

### 5.1 前置条件

- kubeconfig：`~/.kube/config-mac-orbstack`（或你的 LAN 集群配置）
- 已安装 Flink Kubernetes Operator
- 节点能拉取或推送镜像到集群内 registry

### 5.2 一键部署

**准生产（推荐 LAN）**：本机构建 → push 集群 registry → SSH 重启 K3s → 节点 pull → 部署

```bash
export KUBECONFIG=~/.kube/config-mac-orbstack
export K3S_SSH_HOST=192.168.1.68    # 可选，默认从 kubeconfig API 地址推断
export K3S_SSH_USER=felixzhu        # 可选，须能 sudo systemctl restart k3s
bash k8s/apply-gido-k3s-registry.sh
```

流程：`k3s-insecure-registry.yaml` → **restart k3s** → `registry` Deployment → Mac `docker push`（经 port-forward）→ 节点 `crictl pull` 试拉 → `apply-gido-stack.sh` → rollout。

等价：`GIDO_K3S_USE_REGISTRY=1 bash k8s/apply-gido-orbstack.sh`（内部转调上脚本）。

**开发捷径（不经 registry）**：

```bash
bash k8s/apply-gido-orbstack.sh
```

**与 Kind 的差异：**

| 项目 | Kind | LAN K3s registry（准生产） | LAN K3s 本机导入（开发） |
|------|------|---------------------------|-------------------------|
| 目标架构 | 自动 arm64/amd64 | 同上 | 同上 |
| 镜像分发 | `ctr import` | **docker push → 节点 pull** | `k3s ctr import` |
| 环境变量 | `GIDO_KIND_LOAD=1` | `K3S_SSH_HOST` / `K3S_SSH_USER` | `K3S_SSH_HOST`（可选） |

### 5.3 访问

```bash
export KUBECONFIG=~/.kube/config-mac-orbstack
kubectl -n gido port-forward svc/frontend 8080:80
```

局域网其他机器访问需绑定 `0.0.0.0` 或配置 Ingress（`GIDO_APPLY_INGRESS=1`）。

### 5.4 已知问题：registry HTTPS

K3s 默认用 HTTPS 拉 `registry.gido.svc.cluster.local:5000`，集群内 registry 为 HTTP，可能导致 `ImagePullBackOff`。

已提供 `k8s/k3s-insecure-registry.yaml`（HelmChartConfig）；若未生效，在 **K3s 节点**（OrbStack 的 `ubuntu` VM，非 API 地址 192.168.1.68）执行：

```bash
sudo systemctl restart k3s
sudo k3s crictl pull registry.gido.svc.cluster.local:5000/gido-backend:orbstack
```

注意：`k3s crictl` 必须加 **sudo**，否则报 `permission denied` / `crictl.yaml` 找不到。

---

## 6. 文件索引

| 文件 | 用途 |
|------|------|
| `apply-gido-stack.sh` | 主部署入口（构建 + Kind 导入 + apply） |
| `apply-gido-orbstack.sh` | 局域网 K3s 本机导入（开发捷径） |
| `apply-gido-k3s-registry.sh` | 局域网 K3s 准生产：build → push registry → restart k3s → deploy |
| `lib/k3s-registry.sh` | HTTP registry 配置、SSH 重启 K3s、试拉、rollout |
| `create-kind-gido.sh` | 创建 Kind 集群 |
| `kind-gido-config.yaml` | Kind 配置（镜像加速、ingress-ready） |
| `kind-load-mirror-images.sh` | 导入 postgres/busybox/GIDO 镜像到 Kind |
| `lib/kind-image.sh` | Kind 单架构构建与压平 |
| `lib/k3s-image.sh` | K3s 构建与推送到集群 registry |
| `gido.yaml` | GIDO 最小栈清单 |
| `upgrade-flink-operator-1.15.sh` | Operator 升级到 1.15.0 |
| `flink-operator-rbac.yaml` | Operator 跨命名空间 RBAC |
| `legacy/` | 遗留 Session Flink、DS/Doris 示例（默认不部署，见 `legacy/README.md`） |
| `registry.yaml` | 集群内临时镜像仓库（K3s 用） |
| `gido-ingress.yaml` | Ingress（可选） |

---

## 7. 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| `no match for platform` | Mac 拉取多架构 / 错误架构镜像 | 走 `kind-load-mirror-images.sh` 或 `kind_image_build` |
| 部署到错误集群 | 默认 KUBECONFIG 指向 OrbStack | 显式 `KUBECONFIG=~/.kube/kind-gido.yaml` |
| port-forward 断开 | frontend Pod 滚动更新 | 重新 `kubectl port-forward` |
| Flink UI 空白 | 旧 `flink_job_id` 或代理 base href | 重同步作业；确认 `FLINK_OPERATOR_UI_PROXY_ENABLED` |
| JAR 提交失败 | 从未上传 / PVC 未挂载 / artifact 404 | 在 UI 重传 JAR；`kubectl -n gido get pvc gido-jar-artifacts` |
| 运维就绪度 blocked：`FLINK_OPERATOR_ARTIFACT_TOKEN` | Secret 缺 key 或 backend Pod 未重启 | 确认 `gido-secrets` 含该 key；`kubectl -n gido rollout restart deployment/gido-backend`；刷新作业详情 |
| OrbStack ImagePullBackOff | registry HTTP vs K3s HTTPS | `bash k8s/apply-gido-k3s-registry.sh`（含 restart k3s）；或 §5.4 手动 |
| postgres 数据丢失 | `gido.yaml` 使用 emptyDir | 生产改 PVC |

---

## 8. 生产注意事项

- 修改 `gido-secrets` 中 postgres 密码、`SECRET_KEY`、`FLINK_OPERATOR_ARTIFACT_TOKEN`
- postgres 改为 **PVC**（JAR 已用 `gido-jar-artifacts` PVC）
- 配置 Ingress 与 TLS，设置 `FLINK_OPERATOR_UI_URL_TEMPLATE`
- 元数据库说明见根目录 [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md)
