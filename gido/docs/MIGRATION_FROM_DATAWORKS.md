# GIDO 全量重命名说明（原 dataworks）

> 自本版本起，项目以 **玑渡 GIDO** 为唯一产品形态，不再保留 `dataworks` 命名。

## 路由

| 子产品 | 新路径 |
|--------|--------|
| GIDO Batch | `/gido/batch/*`（如 `/gido/batch/studio`） |
| GIDO Stream | `/gido/stream/*` |
| GIDO Serve | `/gido/service/*` |

## 目录与部署

- 代码目录：`bigdata_all/gido/`（原 `dataworks/`）
- 元数据库：PostgreSQL **`gido`**（原 `dataworks` 库需重建或迁移）
- 容器：`gido-backend` / `gido-frontend`
- 环境变量前缀：`GIDO_*`（如 `GIDO_DATABASE_URL`）

## 权限码

- Batch：`gido:batch:*`
- Stream：`gido:stream:*`
- Service：`gido:service:*`

## 升级步骤（无兼容，建议全新库）

```bash
# 1. 重建 PG 库（会清空元数据）
docker compose -f dockerFile/docker-compose.platform.yml exec postgres \
  psql -U root -d postgres -c 'DROP DATABASE IF EXISTS gido; CREATE DATABASE gido;'

# 2. 重建并启动 GIDO
cd gido && docker compose build && docker compose up -d

# 3. 或整平台
cd .. && ./start-platform.sh
```

登录后默认进入 **`/gido/batch/studio`**。
