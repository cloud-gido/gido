# 玑渡 GIDO 开发交接说明

> 供下一位开发者/使用者快速接手。  
> **完整 AI 对话（尽量完整版）**：[`notes/2026-06-05-gido-session-full.md`](notes/2026-06-05-gido-session-full.md)（283 条问题索引 + 全量对话正文）

最后更新：2026-06-05

---

## 1. 项目是什么

- **仓库**：`bigdata_all`，GIDO 代码在 **`gido/`**
- **品牌**：玑渡 GIDO（Batch / Stream / Serve），已去掉 `dataworks` 命名
- **开源**：Apache-2.0，根目录 `LICENSE`、`TRADEMARK.md`、`SECURITY.md` 等

---

## 2. 怎么跑起来

| 场景 | 命令 | 访问 |
|------|------|------|
| **全栈（推荐）** | 仓库根 `./start-platform.sh` | 前端 `http://127.0.0.1:3002` |
| **仅 GIDO** | `cd gido && ./start.sh` | 同上（勿与全栈同时跑） |
| **局域网** | 默认 `GIDO_BIND_HOST=0.0.0.0` | `http://<本机LAN_IP>:3002` |
| **本地前端 dev** | `cd gido/frontend && npm run dev` | `http://127.0.0.1:3003`（与 Docker 3002 错开） |

**Compose 唯一来源**：`gido/docker-compose.yml`；全栈通过 `docker-compose-platform.yml` **include** 引用，不要重复定义 backend/frontend。

**清理端口冲突**：

```bash
bash scripts/reset-gido-docker.sh   # 需 LF 换行；勿用 CRLF
./start-platform.sh
```

**OrbStack 端口残留**（宿主机 curl 与容器内 hash 不一致、容器已删仍能访问 3002）：重启 OrbStack → 再跑 reset + start-platform。

---

## 3. 本阶段已完成的主要改动

### 品牌与 UI

- 登录/关于页：浅色矢量星徽 `GidoBrandHero`，不用黑底 PNG
- Favicon：仅 `favicon.svg`（浅底），已删 `.ico`/黑底 PNG
- 关于页 `/about`：开源信息、维护者、文档链接

### 维护者（关于页）

- Troy · troyzhujingbin@163.com
- Chenghap · chenghap0712@gmail.com  
- 配置：`gido/frontend/src/branding.ts` → `OPEN_SOURCE.maintainers`
- 页面版本标记：`OPEN_SOURCE.aboutRevision`（用于确认前端是否更新）

### 开源合规

- 根目录：`LICENSE`、`NOTICE`、`TRADEMARK.md`、`CONTRIBUTING.md`、`SECURITY.md`、`CHANGELOG.md`
- SPDX 脚本：`python gido/scripts/add_spdx_headers.py`
- CI：`.github/workflows/ci.yml`

### Docker / 部署

- `dataworks` → `gido` 全量重命名（含 `docker-compose.platform.yml`）
- 端口：UI `3002`、API `8001`；绑定 `GIDO_BIND_HOST`（默认 `0.0.0.0` 供局域网）

---

## 4. 关键文件索引

| 用途 | 路径 |
|------|------|
| 品牌常量（前端） | `gido/frontend/src/branding.ts` |
| 品牌常量（后端） | `gido/backend/app/core/brand.py` |
| 路由 | `gido/frontend/src/routes.ts` |
| 唯一 Compose | `gido/docker-compose.yml` |
| 全栈入口 | `docker-compose-platform.yml` |
| 一键启动 | `start-platform.sh` |
| 开源 checklist | `gido/docs/OPEN_SOURCE.md` |
| 部署 SOP | `gido/docs/DEPLOYMENT_SOP.md` |
| 品牌规范 | `gido/docs/BRAND.md` |

---

## 5. 环境变量（根目录 `.env`）

```bash
GIDO_DS_TOKEN=...              # Dolphin
GIDO_DATABASE_URL=...          # 可选，全栈默认连 platform-postgres/gido
GIDO_BIND_HOST=0.0.0.0         # 局域网访问；仅本机可改为 127.0.0.1
GIDO_UI_PORT=3002
GIDO_API_PORT=8001
KAFKA_LAN_HOST=192.168.x.x     # start-platform 会自动检测 en0
```

**勿提交**真实 `.env`；仅 `.env.example`。

---

## 6. 验证清单（接手后 5 分钟）

```bash
# 1. 容器与端口一致
docker ps | grep gido-frontend
curl -s http://127.0.0.1:3002/ | grep 'assets/index'
docker exec gido-frontend sh -c 'wget -qO- http://127.0.0.1/ | grep assets/index'

# 2. 关于页数据在构建产物里
docker exec gido-frontend sh -c "grep -o chenghap0712 /usr/share/nginx/html/assets/index-*.js | head -1"

# 3. 构建
cd gido/frontend && npm run build
cd gido/backend && pytest -q
```

---

## 7. 已知坑

1. **不要同时跑** `gido/docker-compose.yml` 与全栈 compose（同名容器 `gido-frontend`）
2. **OrbStack** 可能缓存 3002 转发；对比宿主机 curl 与容器内 wget 的 JS hash
3. **`scripts/*.sh` 必须 LF 换行**，CRLF 会导致 `set: pipefail: invalid option`
4. 重建前端：`docker compose -f docker-compose-platform.yml build frontend --no-cache && up -d --force-recreate frontend`
5. GitHub 仓库 URL 占位：`branding.ts` 里 `repositoryUrl` 需改成真实地址

---

## 8. 对话 / 记录怎么导出

### A. 仓库文档（推荐，已在做）

- 本文 **`DEV_HANDOFF.md`**（精简交接）
- **[`notes/2026-06-05-gido-session-full.md`](notes/2026-06-05-gido-session-full.md)** — 尽量完整版：执行摘要 + 283 条问题索引 + 全量对话（约 950KB）
- `CHANGELOG.md`、`MIGRATION_FROM_DATAWORKS.md`、`OPEN_SOURCE.md`

### B. Cursor 聊天

- 聊天面板右上角 **⋯** → **Export / Copy**（版本以你当前 Cursor 为准）
- 或手动全选对话 → 粘贴到 `gido/docs/notes/YYYY-MM-DD-session.md` 提交 Git

### C. Agent 完整 JSONL（含工具调用，适合 AI 续聊）

本机路径（Cursor 项目）：

```text
~/.cursor/projects/<project-id>/agent-transcripts/<uuid>.jsonl
```

可用文本编辑器打开；给下一个 **Cursor Agent** 时可在新对话中说：「请先读 `gido/docs/DEV_HANDOFF.md` 和 transcript …」

### D. 固化成 Cursor Rule（长期习惯）

把团队约定写入 `.cursor/rules/` 或用户 Rules，例如：

- 品牌只用「玑渡 GIDO」
- Compose 只改 `gido/docker-compose.yml`
- 改关于页维护者只改 `branding.ts`

---

## 9. 建议下一位开发者第一步

1. 读本文 + `gido/README.md`
2. 复制 `.env.example` → `.env`，填 `GIDO_DS_TOKEN`
3. `./start-platform.sh`
4. 打开 `/about` 确认 `aboutRevision` 与维护者
5. 从 `main` 拉代码后跑 `python gido/scripts/add_spdx_headers.py`（若 CI 报 SPDX）

如有疑问，优先查 `gido/docs/TROUBLESHOOTING_SOP.md`。
