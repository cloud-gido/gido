# 玑渡 GIDO 子项目 — AWS EKS 部署标准操作流程（SOP）

面向：**仓库仅含 `gido/`（前后端双镜像），希望在 AWS EKS 上可入手操作、跑通最小生产形态**的同事。

本地 Docker 与库表初始化仍以 **[DEPLOYMENT_SOP.md](./DEPLOYMENT_SOP.md)** 为准；本文只讲 **EKS + ECR + 对外入口 + 外部依赖（RDS 等）**。

---

## 0. 架构与约束（读一遍再动手）

| 组件 | 本仓库现状 | EKS 上建议 |
|------|------------|------------|
| **Backend** | `backend/Dockerfile`，监听 **8001**，`/health` | `Deployment` + `ClusterIP` **Service** |
| **Frontend** | `frontend/Dockerfile`（Nginx 托管静态资源） | `Deployment` + `ClusterIP` **Service** |
| **API 路径** | `frontend/nginx.conf`：`location /api` → **`http://backend:8001`** | **Kubernetes 中后端 Service 名称必须为 `backend`**，与 Nginx 上游一致；**无需**单独把后端暴露到公网 |
| **PostgreSQL（元数据）** | 外置实例；**`INFRA_GIDO_DB_*` 拆分变量**（推荐）或 **`GIDO_DATABASE_URL` / `DATABASE_URL`** | **RDS PostgreSQL** 或自建；compose 不再内置 PG 容器 |
| **Dolphin / Flink** | 环境变量或界面集成 | 填 **从 Pod 网络可达** 的 URL；暂无可先关 DS 或仅占位 |

**重要**：若改名后端 Service（不叫 `backend`），须同步改 `frontend/nginx.conf` 并**重新构建前端镜像**，或在 Ingress 层做路径拆分（本 SOP 按「Service 名 `backend`」走最短路径）。

---

## 1. 本机 / CI 工具

- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)（`aws configure`）
- `kubectl`（与集群控制面版本相差不超过约 1 个小版本）
- 任选：**eksctl**（上手最快）或 **Terraform**
- **Docker**（构建并 push 镜像）或 **CodeBuild**（云上构建）

---

## 2. AWS 侧先备好的资源

建议顺序：**ECR → RDS → EKS**（EKS 与 RDS 可同 Region；安全组要放行 **EKS 节点/worker → RDS 5432**（PostgreSQL））。

1. **ECR 仓库**（示例名，可自定）  
   - `gido-backend`  
   - `gido-frontend`
2. **RDS PostgreSQL**（或 Aurora PostgreSQL）：库名、账号、连接串。  
   连接串形态与后端一致，例如：

   ```text
   postgresql+psycopg2://用户:密码@<rds-endpoint>:5432/gido
   ```

3. **密钥**：用 **AWS Secrets Manager**（推荐）或 Kubernetes `Secret` 存 **`INFRA_GIDO_DB_SERVICE_PASSWORD`**、**`DATABASE_URL`**（若未用拆分变量）、DS Token、Flink 相关密码等；**不要**把生产密码写进 Git 里的 YAML。

---

## 3. 构建并推送镜像到 ECR

在可执行 Docker 的环境（Region 与 EKS 一致）：

```bash
export AWS_REGION=ap-northeast-1   # 改为你的 Region
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

aws ecr create-repository --repository-name gido-backend --region $AWS_REGION 2>/dev/null || true
aws ecr create-repository --repository-name gido-frontend --region $AWS_REGION 2>/dev/null || true
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $REGISTRY

# 在 AWS 上构建时建议显式使用官方基础镜像（避免默认 DaoCloud 镜像拉取失败）
docker build -t $REGISTRY/gido-backend:latest \
  --build-arg PY_IMAGE=python:3.9-slim \
  -f backend/Dockerfile backend

docker build -t $REGISTRY/gido-frontend:latest \
  --build-arg NODE_IMAGE=node:18-alpine \
  --build-arg NGINX_IMAGE=nginx:alpine \
  -f frontend/Dockerfile frontend

docker push $REGISTRY/gido-backend:latest
docker push $REGISTRY/gido-frontend:latest
```

**版本策略**：生产应用 **镜像 digest** 或 **`:<git-sha>` tag** 固定版本，避免 `latest` 漂移。

---

## 4. 创建 EKS 集群（eksctl 示例）

```bash
eksctl create cluster \
  --name gido \
  --region $AWS_REGION \
  --nodes 2 \
  --node-type m6i.large \
  --with-oidc
```

- `--with-oidc`：为后续 **IRSA**（Pod 访问 AWS API）预留；暂不用也可保留。  
- 节点规格/数量按实际负载与成本调整。

---

## 5. 安装 AWS Load Balancer Controller（对外 HTTP/HTTPS）

1. 按 AWS 官方文档：为 ALB Controller 创建 **IAM Policy**、**IAM Role（IRSA）**，用 Helm 安装 **AWS Load Balancer Controller**。  
2. 确认公有子网已打 **`kubernetes.io/role/elb`** 等标签（以当前文档为准），否则 **Ingress 无法创建 ALB**。

没有该 Controller 时，`Ingress` 资源通常**不会**自动变成对外负载均衡器。

官方入口（检索用）：`AWS Load Balancer Controller` + `EKS`。

---

## 6. Kubernetes 清单要点（自建 `k8s/` 目录）

本仓库**未自带**业务 Pod 的 `k8s/*.yaml`，需在仓库或部署仓库中新增，建议结构示例：

```text
k8s/
  namespace.yaml
  backend-deployment.yaml
  backend-service.yaml      # metadata.name: backend, port → 8001
  frontend-deployment.yaml
  frontend-service.yaml
  ingress.yaml              # 对外：/ → frontend
  secret.yaml               # 勿提交明文；可用 External Secrets 等
```

**Backend `Deployment`**

- 镜像：`$REGISTRY/gido-backend:<tag>`
- `containerPort: 8001`
- 启动命令与 compose 一致即可，例如：  
  `python init_db.py && uvicorn app.main:app --host 0.0.0.0 --port 8001`  
  （若镜像 `CMD` 已包含则可不重复写）
- 环境变量：将 `docker-compose.yml` 中 `backend` 的 `environment` 迁到 `env` / `ConfigMap` / `Secret`（生产去掉 `GIDO_BOOTSTRAP_ADMIN_PASSWORD` 或勿设置）
- **探针**：`GET /health`（与 compose 中 healthcheck 一致）

**Backend `Service`（关键）**

- `metadata.name: **backend**`
- `spec.ports` 指向 Pod 的 **8001**

**Frontend `Deployment` / `Service`**

- 镜像：`$REGISTRY/gido-frontend:<tag>`
- 容器端口 **80**
- `Service`：`ClusterIP`，`port: 80`

**`Ingress`**

- 使用 ALB Controller 的 **annotations**（如 `internet-facing` / scheme、证书 ARN 等，以你集群安装的 Controller 版本文档为准）。
- 路由：`/` → **frontend** Service（浏览器同域访问 `/api`，由 Nginx 反代到 `backend`）。

**大文件与长连接**

- 前端镜像内 Nginx 已配置 **`client_max_body_size 256m`** 与较长 **proxy_read_timeout**（见 `frontend/nginx.conf`）。若经 ALB，仍需核对 ALB / Ingress 侧是否还有 body 或超时限制。

---

## 7. Flink / 集群内 Kubernetes API（易踩坑）

- 若在 **同一 EKS** 内提交 Flink Application 并调 **Kubernetes API**：需要 **集群内 ServiceAccount + RBAC**，或 **IRSA** 等，**不能**依赖 compose 里的 `host.docker.internal`。
- `FLINK_URL`、`FLINK_SQL_GATEWAY_URL` 等：必须填 **从 backend Pod 内能解析且能访问** 的地址（ClusterIP、NLB、内网 Ingress 等）。

---

## 8. 发布与验证

```bash
kubectl apply -f k8s/
kubectl -n gido rollout status deploy/backend    # 命名空间与资源名按你的 YAML
kubectl -n gido rollout status deploy/frontend
kubectl -n gido get ingress
```

1. 从 `kubectl describe ingress` 或 AWS 控制台拿到 **ALB DNS**（或绑定的域名）。  
2. 浏览器打开站点，完成登录与主要接口冒烟。  
3. 验证 **同域 `/api`** 正常（即 Nginx → `backend` 链路）。

---

## 9. 上线后运维清单（摘要）

- **RDS**：备份策略、参数组、升级窗口。  
- **镜像**：CI 中 build →（可选安全扫描）→ push → 更新 Deployment 镜像字段。  
- **密钥轮换**：更新 Secrets Manager / K8s Secret 后 **滚动重启** backend。  
- **可观测性**：Container Insights、应用日志、Tracing（按公司标准选型）。

---

## 10. 建议的第一周节奏（最小闭环）

| 阶段 | 内容 |
|------|------|
| Day 1 | ECR 建好；本地/CI 打镜像并 push；RDS 与业务库、账号就绪 |
| Day 2 | `eksctl` 起集群；安装 **AWS Load Balancer Controller** |
| Day 3 | 手写最小 `Deployment` / `Service` / `Ingress`，**后端 Service 名 `backend`**，打通登录与 `/api` |
| Day 4 | 补全 Secret、探针、`resources`；去掉生产环境 bootstrap 固定管理员密码 |
| Day 5 | 接入 Dolphin/Flink，或明确「暂不可用」范围 |

---

## 11. 相关文档

- 本目录本地与 Docker：**[DEPLOYMENT_SOP.md](./DEPLOYMENT_SOP.md)**  
- 集成排障：**[integration-troubleshooting.md](./integration-troubleshooting.md)**
