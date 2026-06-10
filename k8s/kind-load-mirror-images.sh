#!/usr/bin/env bash
# 在宿主准备单架构镜像并导入 Kind 节点（Mac M 默认 arm64，与 K3s 一致）
#
# 用法：
#   KIND_CLUSTER_NAME=gido bash k8s/kind-load-mirror-images.sh
#   KIND_CLUSTER_NAME=gido bash k8s/kind-load-mirror-images.sh gido-backend:latest gido-frontend:latest
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/kind-image.sh
source "${ROOT}/k8s/lib/kind-image.sh"

MIRROR="${KIND_MIRROR:-docker.m.daocloud.io}"
PLATFORM="${KIND_PLATFORM:-$(gido_default_linux_platform)}"
EXPECTED_ARCH="$(platform_to_arch "${PLATFORM}")"
KIND_SKIP_PULL="${KIND_SKIP_PULL:-}"
KIND_LOAD_FLINK="${KIND_LOAD_FLINK:-}"
KIND_NAME="${KIND_CLUSTER_NAME:-}"
if [[ -z "${KIND_NAME}" ]]; then
  KIND_NAME="$(kind get clusters 2>/dev/null | head -1 || true)"
fi
if [[ -z "${KIND_NAME}" ]]; then
  echo "未找到 Kind 集群；请设置 KIND_CLUSTER_NAME 或先 kind create cluster" >&2
  exit 1
fi

declare -a SPECS=(
  "library/postgres:16-alpine|docker.m.daocloud.io/library/postgres:16-alpine"
  "library/busybox:1.36|docker.m.daocloud.io/library/busybox:1.36"
)
declare -a FLINK_SPECS=(
  "apache/flink:2.0.1-java11|apache/flink:2.0.1-java11"
)

prepare_and_load() {
  local mirror_path="$1"
  local cluster_tag="$2"
  local pull_ref="${MIRROR}/${mirror_path}"
  kind_image_log "--- ${cluster_tag} ---"
  if ! image_exists "${cluster_tag}"; then
    if image_exists "${pull_ref}"; then
      docker tag "${pull_ref}" "${cluster_tag}"
    elif [[ -n "${KIND_SKIP_PULL}" ]]; then
      echo "错误：KIND_SKIP_PULL=1 但宿主无 ${cluster_tag}" >&2
      exit 1
    else
      kind_image_pull_flatten "${cluster_tag}" "${pull_ref}" "${PLATFORM}"
    fi
  else
    kind_image_pull_flatten "${cluster_tag}" "${pull_ref}" "${PLATFORM}"
  fi
  kind_image_import "${cluster_tag}" "${KIND_NAME}"
}

image_exists() {
  docker image inspect "$1" >/dev/null 2>&1
}

kind_image_log "Kind 集群: ${KIND_NAME} | 目标架构: ${PLATFORM}"

for spec in "${SPECS[@]}"; do
  prepare_and_load "${spec%%|*}" "${spec#*|}"
done

if [[ -n "${KIND_LOAD_FLINK}" ]]; then
  kind_image_log "KIND_LOAD_FLINK=1：导入 Flink 作业镜像"
  for spec in "${FLINK_SPECS[@]}"; do
    prepare_and_load "${spec%%|*}" "${spec#*|}"
  done
fi

for extra in "$@"; do
  if ! image_exists "${extra}"; then
    echo "错误：额外镜像 ${extra} 不在宿主 Docker，请先 build" >&2
    exit 1
  fi
  arch="$(docker image inspect "${extra}" --format '{{.Architecture}}' 2>/dev/null || echo "")"
  if [[ "${arch}" != "${EXPECTED_ARCH}" ]]; then
    echo "错误：${extra} 为 ${arch:-?}，Kind 需要 ${EXPECTED_ARCH}（${PLATFORM}）" >&2
    exit 1
  fi
  kind_image_import "${extra}" "${KIND_NAME}"
done

kind_image_log "全部完成。镜像已导入 Kind 节点 ${KIND_NAME}。"
