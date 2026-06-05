#!/usr/bin/env bash
# 在已有 PostgreSQL（Dolphin compose 的 postgres）中创建 Superset 元库
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-$(docker ps -qf 'name=postgres' | head -1)}"
PG_USER="${PG_USER:-root}"

if [[ -z "${PG_CONTAINER}" ]]; then
  echo "未找到 postgres 容器，请设置 PG_CONTAINER=容器ID" >&2
  exit 1
fi

echo "使用容器: ${PG_CONTAINER} (user=${PG_USER})"
docker exec -i "${PG_CONTAINER}" psql -U "${PG_USER}" -d postgres -tc \
  "SELECT 1 FROM pg_database WHERE datname='superset'" | grep -q 1 \
  || docker exec -i "${PG_CONTAINER}" psql -U "${PG_USER}" -d postgres -c "CREATE DATABASE superset;"

echo "OK: database superset 已就绪"
