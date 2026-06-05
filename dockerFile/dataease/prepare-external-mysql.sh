#!/usr/bin/env bash
# 在已有 MySQL 中创建 DataEase 元库（只需一次）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/dataease/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

DE_MYSQL_HOST="${DE_MYSQL_HOST:-127.0.0.1}"
DE_MYSQL_PORT="${DE_MYSQL_PORT:-3306}"
DE_MYSQL_USER="${DE_MYSQL_USER:-root}"
DE_MYSQL_PASSWORD="${DE_MYSQL_PASSWORD:-}"
DE_MYSQL_DB="${DE_MYSQL_DB:-dataease}"

if [[ -z "${DE_MYSQL_PASSWORD}" ]]; then
  echo "请先在 dataease/.env 中设置 DE_MYSQL_PASSWORD" >&2
  exit 1
fi

# 优先用本地 mysql 客户端；否则尝试 mysql 容器
if command -v mysql >/dev/null 2>&1; then
  mysql -h "${DE_MYSQL_HOST}" -P "${DE_MYSQL_PORT}" -u"${DE_MYSQL_USER}" -p"${DE_MYSQL_PASSWORD}" -e \
    "CREATE DATABASE IF NOT EXISTS \`${DE_MYSQL_DB}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
else
  MC="${MYSQL_CONTAINER:-$(docker ps --format '{{.Names}}' | grep -i mysql | head -1)}"
  if [[ -z "${MC}" ]]; then
    echo "未找到 mysql 客户端或 MySQL 容器，请手动建库：CREATE DATABASE dataease ..." >&2
    exit 1
  fi
  docker exec -i "${MC}" mysql -u"${DE_MYSQL_USER}" -p"${DE_MYSQL_PASSWORD}" -e \
    "CREATE DATABASE IF NOT EXISTS \`${DE_MYSQL_DB}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
fi

echo "OK: database ${DE_MYSQL_DB} 已就绪"
