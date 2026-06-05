# 玑渡 GIDO 从 Git 拉取到部署 — 标准操作流程（SOP）

面向：**从仓库克隆代码后，在全新环境跑起 GIDO（后端 + 前端 + PostgreSQL 元数据库），并完成库表与种子数据初始化**的同事。

更完整的大数据平台（Dolphin、Flink、网络等）仍以仓库根目录 **`README.md`**、**`DEPLOYMENT_GUIDE.md`**、**`start-platform.sh`** 为准；本文聚焦 **本目录 `gido/`** 的部署与库初始化。

**AWS EKS 部署**（ECR、集群、Ingress、与 compose 的差异）见 **[EKS-DEPLOYMENT-SOP.md](./EKS-DEPLOYMENT-SOP.md)**。

---

## 1. 仓库与目录

| 方式 | 说明 |
|------|------|
| **推荐** | 克隆整个 **`bigdata_all`** 仓库，需要 GIDO 时进入 **`gido/`**。 |
| **仅拷贝子目录** | 只拿 `gido/` 也可，但须**自备 PostgreSQL**（或自建 RDS/Aurora），并自行处理 **`docker-compose.yml` 中的外部 Docker 网络**（见 §4）。 |

---

## 2. 前置条件

- **Git**
- **Docker 20.10+**、**Docker Compose 2+**（若使用本目录下的 `docker-compose.yml`；**元数据库需自备 PostgreSQL**，在上一级 `.env` 配置 **`GIDO_DATABASE_URL`**，或按运维规范配置 **`INFRA_GIDO_DB_*`** 拆分变量；与本仓库 **Dolphin** 同机 PG 时，compose 默认示例为宿主机 **`host.docker.internal:5432`**、库名 **`gido`**、账号与 **`dockerFile/docker-compose.dolphin.yml`** 中 PG 一致，可按实际改）
- **PostgreSQL 12+**（推荐 14/15；业务元数据库，默认库名 **`gido`**）
- **可选回退**：仍可将 **`DATABASE_URL`** 设为 **`mysql+pymysql://...`** 使用 MySQL 存元数据（需 `pymysql`；自动建库见 `backend/app/core/database.py`）
- **可选**：DolphinScheduler（工作流）、Flink JM + SQL Gateway（实时 SQL）；不配则相关功能不可用或需在界面/环境变量中再接入

---

## 3. PostgreSQL 与库名

1. 准备可访问的 PostgreSQL 实例与账号；账号建议具备 **建库权限**（或先手工 `CREATE DATABASE gido`）。后端在首次连接时会尝试：若库不存在且能连上 **`postgres`** 维护库，则 **`CREATE DATABASE`**（见 `backend/app/core/database.py` 中 **`_ensure_postgresql_database`**）。
2. 连接方式二选一：  
   - **拆分变量（推荐生产）**：`INFRA_GIDO_DB_SERVICE_URL`（无凭据）、`INFRA_GIDO_DB_SERVICE_USER`、`INFRA_GIDO_DB_SERVICE_PASSWORD`、`INFRA_GIDO_DB_URL`（库名）；可选 `INFRA_GIDO_DB_SERVICE_READER`。后端组装为 SQLAlchemy 使用的 `postgresql+psycopg2://...`（见 `backend/app/core/config.py` 中 **`resolved_database_url`**）。  
   - **单一 URL（本地/兼容）**：`DATABASE_URL` / `GIDO_DATABASE_URL`，格式示例：

   ```text
   postgresql+psycopg2://用户名:密码@主机:5432/gido
   ```

3. **无需手工导入建表 SQL**：表结构、增量列、RBAC 种子数据由 **`init_db.py`** 与 **`app/main.py` 启动生命周期** 中的幂等迁移完成（升级代码后重启即可自动补表/补列）。迁移脚本已区分 **MySQL / PostgreSQL / SQLite**（`app/services/rbac_seed.py`）。

---

## 4. 环境变量与 `.env` 位置（Docker）

本目录 **`docker-compose.yml`** 中：

- `env_file: ../.env` 表示 **`.env` 必须放在 `gido` 的上一级目录**（例如 monorepo 根 `bigdata_all/.env`），在 **`gido/`** 下执行 `docker compose` 时才会被加载。

**建议在 `.env` 或宿主机环境中配置的关键项：**

| 变量（示例） | 含义 |
|--------------|------|
| `GIDO_DATABASE_URL` | 元数据库连接串（**强烈建议显式配置**；compose 中 `DATABASE_URL` 优先取该变量；未设时 fallback 为 Dolphin 同机 PG + 库 **`gido`**) |
| `INFRA_GIDO_DB_SERVICE_URL` | **生产推荐**：PG 地址（**无**账号密码），如 `pg.internal:5432` 或 `postgresql://pg.internal:5432/gido`；与下列三项同时齐全时 **优先于** `DATABASE_URL` |
| `INFRA_GIDO_DB_SERVICE_USER` / `INFRA_GIDO_DB_SERVICE_PASSWORD` | 读写账号与密码（密码可进 Secret；可为空串） |
| `INFRA_GIDO_DB_URL` | 库名，如 `gido` |
| `INFRA_GIDO_DB_SERVICE_READER` | （可选）只读账号用户名；当前进程仍用读写账号 |
| `GIDO_BOOTSTRAP_ADMIN_PASSWORD` | 仅排障/首次统一 **admin** 密码；**生产务必去掉或置空** |
| `GIDO_DS_ENABLED`、`GIDO_DS_URL`、`GIDO_DS_TOKEN`、`GIDO_DS_UI_URL` 等 | DolphinScheduler；与真实 DS 对齐 |
| `GIDO_FLINK_*` | Flink JM / Gateway / UI 等；可与「系统管理 → 集成」互补 |

**外部 Docker 网络（可选）**：旧版文档要求 `bigdata_all_data-platform-network`；当前 **`gido/docker-compose.yml` 默认不再挂载外部网络**。全栈请用根目录 **`./start-platform.sh`**（`include` 本目录 compose）。若仍单独跑 Dolphin 分栈 compose 并需容器互通，可自行 `docker network create ...` 后扩展 compose。

---

## 5. 数据库初始化怎么做（核心步骤）

### 方式 A：Docker Compose（与仓库现有一致）

在 **`gido/`** 目录执行（且上一级已放置 `.env`、**外置 PostgreSQL 已启动且 backend 容器可访问**、外部 Docker 网络已就绪）：

```bash
docker compose build
docker compose up -d
```

`backend` 容器启动命令为：

```text
python init_db.py && uvicorn app.main:app --host 0.0.0.0 --port 8001
```

含义：

1. **`python init_db.py`**（`backend/init_db.py`）  
   - `Base.metadata.create_all`  
   - 执行一批 **`migrate_*`**（`app/services/rbac_seed.py`）  
   - **`run_rbac_bootstrap`**（权限/角色等）  
   - 创建 **`admin`**、默认工作空间 **`infras`** 及成员（若不存在）
2. **`uvicorn`** 启动 API；应用 **`lifespan`** 内仍会执行迁移/bootstrap，与已有库 **幂等** 兼容。

**验收：**

- API：`http://localhost:8001/docs`（或映射后的主机与端口）
- 健康检查：`http://localhost:8001/health`
- 前端（compose 默认）：`http://localhost:3002`

### 方式 B：本机 Python（研发）

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

在 **`backend/.env`** 或上一级 **`.env`** 中配置 **`DATABASE_URL`** 或 **`INFRA_GIDO_DB_*`**（及其他需要项，见 `app/core/config.py`）。可参考 **`backend/env.example`**。

```bash
python init_db.py
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

**注意：** 全新空库建议 **先执行一次 `init_db.py`** 再长期只跑 `uvicorn`，避免缺表或缺种子数据。

### 前端（本地）

```bash
cd frontend
npm install
npm run dev
```

开发时 Vite 将 **`/api`** 代理到 **`http://127.0.0.1:8001`**（见 `frontend/vite.config.ts`）。生产构建部署需自行配置 Nginx 或 **`VITE_API_ORIGIN`**（见 `frontend/src/api/request.ts`）。

---

## 6. 首次登录与运维要点

| 项 | 说明 |
|----|------|
| 默认账号 | **`admin`**；初始密码以 **`init_db.py` 控制台输出** 或环境变量 **`GIDO_BOOTSTRAP_ADMIN_PASSWORD` / `RESET_ADMIN_PASSWORD`** 为准（详见 `init_db.py` 与 `app/core/config.py` 注释） |
| 生产 | 修改强密码、**关闭** bootstrap 类环境变量、修改 **`SECRET_KEY`**、HTTPS 与防火墙 |
| 集成 | Dolphin、Flink 可在 **系统管理 → 集成** 配置，也可继续用环境变量覆盖 |
| 升级 | `git pull` 后重启 backend；迁移对已有库 **加表/加列**，一般无需手工 SQL |
| 备份 | 定期备份 PostgreSQL 中 **`gido` 库**（`pg_dump` 或云厂商快照）；`.env` 与密钥勿提交 Git |

---

## 7. 常见问题（排障）

**首选**：[**TROUBLESHOOTING_SOP.md**](./TROUBLESHOOTING_SOP.md)（按现象：Dolphin 401、Master 137、SQL 发布 SHELL、后端 Restarting 等；含 **2026-05-20** 案例速查）。

1. **容器内连不上外置 PostgreSQL**：`DATABASE_URL` 里主机须写 **`host.docker.internal`**（不要用 `127.0.0.1` 指宿主机，容器内那是容器自己）；已配 `extra_hosts: host-gateway`。若 PG 跑在别的机器，写可达的内网 IP/DNS。
2. **两台 PostgreSQL / 5432 冲突**：只保留 **`dockerFile/docker-compose.dolphin.yml`** 内的 `postgres`（已映射宿主机 **5432**）。若仍有旧容器 **`dolphinscheduler-postgresql`** 占位，先执行 **`bash dockerFile/remove-legacy-postgres-on-5432.sh`**，再 `docker compose -f dockerFile/docker-compose.dolphin.yml up -d`。
3. **Compose 报错：network not found**：创建 §4 中的外部网络，或修改 compose。  
4. **找不到 `.env`**：确认文件在 **`gido` 上一级**，或修改 `docker-compose.yml` 的 `env_file`。  
5. **暂不部署 Dolphin**：可设 **`GIDO_DS_ENABLED=false`**，先起 GIDO，再接 DS。  
6. **Flink 实时功能**：配置 Gateway/JM 或集成页；排障可参考 **`docs/integration-troubleshooting.md`**。  
7. **仍使用 MySQL 存元数据**：将 **`DATABASE_URL` / `GIDO_DATABASE_URL`** 设为 `mysql+pymysql://...`；迁移与自动建库仍支持（见 `database.py`）。
8. **演进与排障全文（PG / Doris / Dolphin / 工作流 / 502 等）**：见 **`docs/OPS_EVOLUTION_AND_TROUBLESHOOTING.md`**。  
9. **Dolphin Token 401 / Master OOM(137) / 发布 DS**：见 **`docs/TROUBLESHOOTING_SOP.md`** 与 **`docs/integration-troubleshooting.md` §3**。

---

## 8. 一页检查表（交付自检）

- [ ] 已克隆正确分支/标签  
- [ ] PostgreSQL 可连，`INFRA_GIDO_DB_*` 四项齐全或 `GIDO_DATABASE_URL` / `DATABASE_URL` 正确（外置库 + 容器内主机名无误）  
- [ ] `.env` 路径与 `docker-compose.yml` 中 `env_file` 一致（Docker 场景）  
- [ ] 外部 Docker 网络已创建或 compose 已调整  
- [ ] 已执行 **`init_db.py`**（或 Docker 启动已自动执行）  
- [ ] `/health`、`/docs` 可访问  
- [ ] 前端可打开并使用 **admin** 登录  
- [ ] 生产项：关闭 bootstrap 密码、强密钥、网络与备份策略已落实  

---

## 9. 相关文件索引

| 文件 | 作用 |
|------|------|
| **`docs/TROUBLESHOOTING_SOP.md`** | **按现象排障 SOP**（401/137/SQL 发布/后端重启等，含日案例表） |
| **`docs/OPS_EVOLUTION_AND_TROUBLESHOOTING.md`** | **运维与排障整体梳理**（PG/Doris/Dolphin/工作流/数据源/502 等演进记录） |
| `docker-compose.yml` | 后端/前端、**外置 PG** 连接串（`host.docker.internal`）、`init_db` + `uvicorn` |
| `backend/init_db.py` | 建表、迁移、RBAC bootstrap、admin 与默认工作空间 |
| `backend/app/main.py` | 应用启动时迁移与 `refresh_*` 客户端 |
| `backend/app/core/config.py` | 环境变量与默认值说明 |
| `backend/app/core/database.py` | PostgreSQL/MySQL 自动建库、引擎与 Session |
| `docs/integration-troubleshooting.md` | 集成侧排障 |
