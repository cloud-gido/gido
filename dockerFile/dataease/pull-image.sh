#!/usr/bin/env bash
# 拉取 DataEase 镜像（不拉 MySQL）
# 成功后在 dataease/.env 写入 DATAEASE_IMAGE=... 再启动
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/dataease/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ -n "${DATAEASE_IMAGE:-}" ]]; then
  candidates=("${DATAEASE_IMAGE}")
else
  candidates=(
    "registry.cn-qingdao.aliyuncs.com/dataease/dataease:v2.10.11"
    "registry.cn-qingdao.aliyuncs.com/dataease/dataease:v2.10.10"
    "registry.cn-qingdao.aliyuncs.com/dataease/dataease:v2.10.14"
    "registry.cn-qingdao.aliyuncs.com/dataease/dataease:v2.10.5"
  )
fi

for img in "${candidates[@]}"; do
  echo "==> docker pull ${img}"
  if docker pull "${img}"; then
    echo ""
    echo "OK: ${img}"
    echo ""
    echo "启动："
    echo "  docker compose -f docker-compose.dataease.yml up -d"
    echo ""
    echo "或写入 dataease/.env："
    echo "  DATAEASE_IMAGE=${img}"
    echo "  DATAEASE_PULL_POLICY=never"
    exit 0
  fi
  echo "失败，尝试下一个 tag…" >&2
done

echo "所有镜像源均失败。请检查网络，或从 DataEase 官网下载离线包导入镜像。" >&2
exit 1
