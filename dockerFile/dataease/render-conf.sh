#!/usr/bin/env bash
# 根据 dataease/.env 生成 conf/application.yml（连接已有 MySQL）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/dataease/.env"
TEMPLATE="${ROOT}/dataease/conf/application.yml.template"
OUT="${ROOT}/dataease/conf/application.yml"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

export DE_MYSQL_HOST="${DE_MYSQL_HOST:-host.docker.internal}"
export DE_MYSQL_PORT="${DE_MYSQL_PORT:-3306}"
export DE_MYSQL_DB="${DE_MYSQL_DB:-dataease}"
export DE_MYSQL_USER="${DE_MYSQL_USER:-root}"
export DE_MYSQL_PASSWORD="${DE_MYSQL_PASSWORD:-}"
export DE_PORT="${DE_PORT:-8100}"

if [[ -z "${DE_MYSQL_PASSWORD}" ]] || [[ "${DE_MYSQL_PASSWORD}" == *"请改"* ]]; then
  echo "请先在 dataease/.env 中设置 DE_MYSQL_PASSWORD（与现有 MySQL root 密码一致）" >&2
  exit 1
fi

if [[ ! -f "${TEMPLATE}" ]]; then
  echo "缺少模板: ${TEMPLATE}" >&2
  exit 1
fi

python3 - "${TEMPLATE}" "${OUT}" <<'PY'
import os
import sys

template_path, out_path = sys.argv[1], sys.argv[2]
keys = (
    "DE_MYSQL_HOST",
    "DE_MYSQL_PORT",
    "DE_MYSQL_DB",
    "DE_MYSQL_USER",
    "DE_MYSQL_PASSWORD",
    "DE_PORT",
)
with open(template_path, encoding="utf-8") as f:
    text = f.read()
for k in keys:
    text = text.replace("${" + k + "}", os.environ.get(k, ""))
with open(out_path, "w", encoding="utf-8") as f:
    f.write(text)
PY

echo "OK: ${OUT} -> mysql://${DE_MYSQL_USER}@${DE_MYSQL_HOST}:${DE_MYSQL_PORT}/${DE_MYSQL_DB}"
