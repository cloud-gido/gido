# 玑渡 GIDO

**玑渡 GIDO** 开源大数据开发与治理子系统（FastAPI + React/Vite），位于本仓库 **`gido/`** 目录。

| 子产品 | 路由 | 说明 |
|--------|------|------|
| **GIDO Batch**（玑渡·批） | `/gido/batch/*` | 离线开发、工作流、调度 |
| **GIDO Stream**（玑渡·流） | `/gido/stream/*` | Flink 实时 SQL / JAR（Flink Kubernetes Operator） |
| **GIDO Serve**（玑渡·服） | `/gido/service/*` | 数据服务 API |

品牌规范见 [docs/BRAND.md](docs/BRAND.md)。代码常量：前端 `frontend/src/branding.ts`，后端 `backend/app/core/brand.py`。

## GIDO Stream 菜单

| 菜单 | 路径 | 说明 |
|------|------|------|
| 作业开发 | `/gido/stream/studio` | SQL / JAR 编辑、提交；可选 Operator 集群与运行时镜像 |
| 作业运维 | `/gido/stream/monitor` | 运行中作业监控 |
| **Operator 集群** | `/gido/stream/operator-clusters` | **多套 Flink Operator 目标集群**（命名空间、镜像、kube context 等） |
| 发布审批 | `/gido/stream/approval` | 流作业发布审批 |
| Flink 运行概览 | `/gido/stream/overview` | Operator `FlinkDeployment` 聚合与健康 |

> 旧路径 `/gido/stream/flink-sessions`（Session 模式）已废弃，自动重定向至运行概览。

### 多套 Operator 集群

工作空间内可配置 **多套 Flink Kubernetes Operator 目标集群**（表 `dw_flink_operator_profiles`），用法类似数据源「多连接」：

```text
平台 .env 默认（Settings）
  → Operator Profile（工作空间级，UI「Operator 集群」或 API）
    → 作业级 streaming_properties.operator_runtime_image（作业开发「运行时镜像」）
```

- **UI**：实时开发 → **Operator 集群** → 新增 / 编辑；作业开发页 **Operator 集群** 下拉选择，旁链「管理」跳转配置页。
- **API**：`GET/POST/PUT/DELETE /api/streaming/flink-operator-profiles`（需 `gido:stream:write` 增删改）。
- **配置示例**：生产见 [config/flink-operator.production.env.example](config/flink-operator.production.env.example)；本机 Kind 见 [config/flink-operator.kind-local.env.example](config/flink-operator.kind-local.env.example)。
- **参数详解**：见 [docs/OPERATOR_CLUSTER_PROFILE.md](docs/OPERATOR_CLUSTER_PROFILE.md)（每个表单字段含义、继承规则、.env 对照与示例）。
- **K8s 前置**：Operator 1.15+、CRD `v2_2`、RBAC（`k8s/flink-operator-rbac.yaml`）、作业命名空间与 `imagePullSecrets`（私有镜像时）。

**生产流作业（EKS / K8s）**：默认 **Flink Kubernetes Operator** 提交；JAR/SQL 制品可配置 **S3 持久化**（`FLINK_OPERATOR_JAR_S3_PREFIX`）。CDC→Paimon 见 [../docs/CDC_PAIMON_EKS.md](../docs/CDC_PAIMON_EKS.md)。

## 文档

| 文档 | 说明 |
|------|------|
| [../docs/PRODUCT_OVERVIEW.md](../docs/PRODUCT_OVERVIEW.md) | **产品截图与 5 分钟体验指南** |
| [../docs/PRODUCT_MATURITY.md](../docs/PRODUCT_MATURITY.md) | **功能完整度与部署边界** |
| [../docs/CDC_PAIMON_EKS.md](../docs/CDC_PAIMON_EKS.md) | **EKS 生产 CDC→Paimon + S3 制品库** |
| [../docs/FLINK_ARCHITECTURE.md](../docs/FLINK_ARCHITECTURE.md) | Flink Operator 架构说明 |
| [../k8s/README.md](../k8s/README.md) | Kind / K3s 部署 |
| [../k8s/eks/README.md](../k8s/eks/README.md) | EKS 示例清单（IRSA 等） |
| [docs/EKS-DEPLOYMENT-SOP.md](docs/EKS-DEPLOYMENT-SOP.md) | **AWS EKS 部署 SOP** |
| [docs/OPEN_SOURCE.md](docs/OPEN_SOURCE.md) | **开源发布、合规与防侵权** |
| [../.github/workflows/ci.yml](../.github/workflows/ci.yml) | GitHub Actions CI（构建 + 合规） |
| [docs/MIGRATION_FROM_DATAWORKS.md](docs/MIGRATION_FROM_DATAWORKS.md) | 历史命名迁移说明 |
| [docs/DEPLOYMENT_SOP.md](docs/DEPLOYMENT_SOP.md) | **从 Git 拉取到部署的标准流程（含数据库初始化）** |
| [docs/OPERATOR_CLUSTER_PROFILE.md](docs/OPERATOR_CLUSTER_PROFILE.md) | **Operator 集群 Profile 各参数详解** |
| [docs/TROUBLESHOOTING_SOP.md](docs/TROUBLESHOOTING_SOP.md) | **按现象排障 SOP**（401/137/发布 DS 等） |
| [docs/integration-troubleshooting.md](docs/integration-troubleshooting.md) | 集成（Dolphin / Flink / Kafka 等）排障 |
| [docs/flink-ops-handoff-template.md](docs/flink-ops-handoff-template.md) | Flink 运维交接模板 |

## 与整仓的关系

一键拉起整平台（含 Dolphin、Kafka、Flink Session、PostgreSQL 等）请参考仓库根目录：

- [../README.md](../README.md)
- [../DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md)（文档索引）
- [../start-platform.sh](../start-platform.sh)

仅部署本目录时，请严格阅读 **`docs/DEPLOYMENT_SOP.md`** 中关于 **`.env` 路径**、**PostgreSQL 元数据库** 与 **Docker 网络** 的说明。

Compose 定义：**仅** `gido/docker-compose.yml`；全栈 `docker-compose-platform.yml` 通过 `include` 引用本文件，不再重复定义 backend/frontend。

## 快速启动

### 前置

1. Docker 20.10+、Docker Compose V2
2. 仓库根目录复制环境模板：`cp .env.example .env`（Windows：`Copy-Item .env.example .env`）
3. **PostgreSQL** 可达（库名 `gido`）。全栈由 `start-platform.sh` 自带；**仅 GIDO** 时需自备 PG（见下）

### 场景对照

| 场景 | 命令 | 说明 |
|------|------|------|
| **全栈**（PG + Kafka + Flink Session + Dolphin + GIDO） | 仓库根 `./start-platform.sh` | 推荐首次体验；需 Git Bash / Linux / macOS |
| **仅 GIDO**（backend + frontend） | 本目录 `./start.sh` | 勿与全栈同时跑（容器名冲突） |
| **改前端后生效** | `docker compose up -d --build frontend` | 在本目录执行，并 `--env-file ../.env` |
| **本地 Python 后端** | `cd backend && pip install -r requirements.txt && python init_db.py && uvicorn app.main:app --reload --port 8001` | 元库 URL 见 `.env` |
| **本地 Vite 前端** | `cd frontend && npm install && npm run dev` | `/api` 代理到 `8001` |

**仅 GIDO + 自备 PostgreSQL（示例）**

```bash
# 1) 启动 PG（首次；与全栈同账号示例）
docker run -d --name platform-postgres \
  -e POSTGRES_USER=root -e POSTGRES_DB=dolphinscheduler \
  -e POSTGRES_PASSWORD='DolphinPgDev!72' \
  -p 5432:5432 \
  -v gido-postgres-data:/var/lib/postgresql/data \
  -v "$(pwd)/../dockerFile/postgres/init:/docker-entrypoint-initdb.d:ro" \
  postgres:16-alpine

# 2) 启动 GIDO（在 gido/ 目录）
./start.sh
```

Windows PowerShell 仅 GIDO：

```powershell
docker compose -f gido/docker-compose.yml --env-file .env up -d --build
```

### 访问地址

| 服务 | URL |
|------|-----|
| 前端 | http://127.0.0.1:3002 |
| API / Swagger | http://127.0.0.1:8001/docs |
| 健康检查 | http://127.0.0.1:8001/health |
| Operator 集群管理 | http://127.0.0.1:3002/gido/stream/operator-clusters |

默认管理员：**`admin`** / **`admin123`**（生产务必修改 `GIDO_BOOTSTRAP_ADMIN_PASSWORD`）。

### 常用运维

```bash
docker compose logs -f backend          # 本目录
docker compose up -d --build frontend   # 仅重建前端
docker compose down                     # 停止 GIDO
./start.sh --recreate                   # 改环境变量后强制重建
```

**说明**：部分 Docker Compose 版本（如 2.27+）执行全栈 `docker compose -f docker-compose-platform.yml` 可能报 `services.backend conflicts with imported resource`；可改用 `./start-platform.sh`（Git Bash），或 **仅 GIDO + 自备 PG** 方式开发。

## 许可证与品牌

| 文档 | 说明 |
|------|------|
| [../LICENSE](../LICENSE) | 源代码：**Apache-2.0** |
| [../TRADEMARK.md](../TRADEMARK.md) | 「玑渡 / GIDO / Logo」商标政策 |
| [docs/OPEN_SOURCE.md](docs/OPEN_SOURCE.md) | 开源发布与安全自查 |

Fork 与商用代码请遵守 Apache-2.0；使用官方名称与 Logo 见 [TRADEMARK.md](../TRADEMARK.md)。

应用内 **关于页**：登录后右上角账号菜单 →「关于 GIDO」，或直接访问 `/about`（无需登录）。
