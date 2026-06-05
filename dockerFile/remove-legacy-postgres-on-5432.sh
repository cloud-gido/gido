#!/usr/bin/env bash
# 停止并删除常见「多余」PostgreSQL 容器（占用宿主机 5432），以便本仓库
# dockerFile/docker-compose.dolphin.yml 中的 postgres 能绑定 5432。
# 仅处理固定容器名；若有数据需保留请先 docker inspect / pg_dump。
set -euo pipefail
LEGACY_NAMES=(dolphinscheduler-postgresql)
for name in "${LEGACY_NAMES[@]}"; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    echo "Removing legacy container: $name"
    docker rm -f "$name"
  else
    echo "No container named $name (skip)"
  fi
done
echo "Done. Then: docker compose -f dockerFile/docker-compose.dolphin.yml up -d"
