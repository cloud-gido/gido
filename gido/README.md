# 玑渡 GIDO

**玑渡 GIDO** 开源大数据开发与治理子系统（FastAPI + React/Vite），位于本仓库 **`gido/`** 目录。

| 子产品 | 路由 | 说明 |
|--------|------|------|
| **GIDO Batch**（玑渡·批） | `/gido/batch/*` | 离线开发、工作流、调度 |
| **GIDO Stream**（玑渡·流） | `/gido/stream/*` | Flink 实时 SQL / JAR |
| **GIDO Serve**（玑渡·服） | `/gido/service/*` | 数据服务 API |

品牌规范见 [docs/BRAND.md](docs/BRAND.md)。代码常量：前端 `frontend/src/branding.ts`，后端 `backend/app/core/brand.py`。

## 文档

| 文档 | 说明 |
|------|------|
| [docs/DEV_HANDOFF.md](docs/DEV_HANDOFF.md) | **开发交接 / 会话沉淀**（给下一位开发者） |
| [docs/OPEN_SOURCE.md](docs/OPEN_SOURCE.md) | **开源发布、合规与防侵权** |
| [../.github/workflows/ci.yml](../.github/workflows/ci.yml) | GitHub Actions CI（构建 + 合规） |
| [docs/MIGRATION_FROM_DATAWORKS.md](docs/MIGRATION_FROM_DATAWORKS.md) | 历史命名迁移说明 |
| [docs/DEPLOYMENT_SOP.md](docs/DEPLOYMENT_SOP.md) | **从 Git 拉取到部署的标准流程（含数据库初始化）** |
| [docs/TROUBLESHOOTING_SOP.md](docs/TROUBLESHOOTING_SOP.md) | **按现象排障 SOP**（401/137/发布 DS 等） |
| [docs/integration-troubleshooting.md](docs/integration-troubleshooting.md) | 集成（Dolphin / Flink / Kafka 等）排障 |
| [docs/flink-ops-handoff-template.md](docs/flink-ops-handoff-template.md) | Flink 运维交接模板 |

实时侧 **多套 Flink 物理集群**：菜单 **「Flink 集群连接」**（`/gido/stream/flink-sessions`），在**平台默认**之上按字段覆写，与数据源「多连接」用法类似。

## 与整仓的关系

一键拉起整平台（含 Dolphin、Kafka、网络等）请参考仓库根目录：

- `../README.md`
- `../DEPLOYMENT_GUIDE.md`
- `../start-platform.sh`

仅部署本目录时，请严格阅读 **`docs/DEPLOYMENT_SOP.md`** 中关于 **`.env` 路径**、**外部 Docker 网络** 与 **PostgreSQL 元数据库** 的说明。

## 快速命令（摘要）

| 场景 | 命令 |
|------|------|
| **全栈**（推荐） | 仓库根 `./start-platform.sh` |
| **仅 GIDO** | 本目录 `./start.sh`（勿与全栈同时跑） |

Compose 定义：**仅** `gido/docker-compose.yml`；全栈 `docker-compose-platform.yml` 通过 `include` 引用，不再重复定义 backend/frontend。

```bash
# docker compose up -d

# 仅 GIDO（自备 PG；勿与全栈同时启动）
./start.sh

# 本地后端
cd backend && pip install -r requirements.txt && python init_db.py && uvicorn app.main:app --reload --port 8001

# 本地前端
cd frontend && npm install && npm run dev
```

## 许可证与品牌

| 文档 | 说明 |
|------|------|
| [../LICENSE](../LICENSE) | 源代码：**Apache-2.0** |
| [../TRADEMARK.md](../TRADEMARK.md) | 「玑渡 / GIDO / Logo」商标政策 |
| [docs/OPEN_SOURCE.md](docs/OPEN_SOURCE.md) | 开源发布与安全自查 |

Fork 与商用代码请遵守 Apache-2.0；使用官方名称与 Logo 见 [TRADEMARK.md](../TRADEMARK.md)。

应用内 **关于页**：登录后右上角账号菜单 →「关于 GIDO」，或直接访问 `/about`（无需登录）。
