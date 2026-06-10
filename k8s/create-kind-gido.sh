#!/usr/bin/env bash

# 用国内镜像加速配置创建 Kind 集群（context 名一般为 kind-gido 或 kind-<name>）
#
# 用法：
#   bash k8s/create-kind-gido.sh
#   KIND_CLUSTER_NAME=my-gido bash k8s/create-kind-gido.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="${KIND_CLUSTER_NAME:-gido}"
CONFIG="${ROOT}/k8s/kind-gido-config.yaml"

if kind get clusters 2>/dev/null | grep -qx "${NAME}"; then
  echo "Kind 集群 ${NAME} 已存在；若要重建：kind delete cluster --name ${NAME}" >&2
  exit 1
fi

# 覆盖 config 里的 name 字段（kind 以 --name 为准，此处保持文件内一致）
sed "s/^name: .*/name: ${NAME}/" "${CONFIG}" > /tmp/kind-gido-config.$$.yaml
trap 'rm -f /tmp/kind-gido-config.$$.yaml' EXIT

echo "==> kind create cluster --name ${NAME}（containerd 镜像加速已写入 config）"
kind create cluster --name "${NAME}" --config "/tmp/kind-gido-config.$$.yaml"

echo ""
echo "集群 ${NAME} 已创建。containerd 会把 docker.io 等请求转发到 DaoCloud。"
echo "仍建议首次部署前执行：KIND_CLUSTER_NAME=${NAME} bash k8s/kind-load-mirror-images.sh"
