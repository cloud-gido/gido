#!/usr/bin/env bash
# 拉取 Superset 镜像（优先 DaoCloud，避免 registry-1.docker.io 超时）
set -euo pipefail

TAG="${SUPERSET_TAG:-4.0.2}"
MIRRORS=(
  "docker.m.daocloud.io/apache/superset:${TAG}"
  "m.daocloud.io/docker.io/apache/superset:${TAG}"
  "apache/superset:${TAG}"
)

for img in "${MIRRORS[@]}"; do
  echo "==> docker pull ${img}"
  if docker pull "${img}"; then
    echo "OK: ${img}"
    echo ""
    echo "请在 superset/.env 中设置："
    echo "SUPERSET_IMAGE=${img}"
    exit 0
  fi
  echo "失败，尝试下一个源…" >&2
done

echo "所有镜像源均失败。可配置 Docker 镜像加速后重试，见：https://docs.daocloud.io/community/mirror/" >&2
exit 1
