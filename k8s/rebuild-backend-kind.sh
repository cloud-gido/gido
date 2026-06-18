#!/usr/bin/env bash
# 重建 gido-backend 并导入 Kind、滚动重启
#
#   bash k8s/rebuild-backend-kind.sh              # 全量构建（需拉基础镜像 / PyPI）
#   GIDO_BACKEND_INCREMENTAL=1 bash k8s/rebuild-backend-kind.sh   # 仅复制 app/（网络差时）
#
set -euo pipefail
export PATH="${HOME}/.local/bin:/c/Windows/System32:${PATH}"
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/kind-gido.yaml}"
export KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-gido}"
export GIDO_BUILD_PLATFORM="${GIDO_BUILD_PLATFORM:-linux/amd64}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${GIDO_BACKEND_IMAGE:-gido-backend:latest}"

if [[ "${GIDO_BACKEND_INCREMENTAL:-}" == "1" ]]; then
  echo "==> 增量构建 ${TAG}（Dockerfile.incremental）"
  docker build --platform "${GIDO_BUILD_PLATFORM}" \
    -f "${ROOT}/gido/backend/Dockerfile.incremental" \
    -t "${TAG}" \
    "${ROOT}/gido/backend"
else
  # shellcheck source=lib/kind-image.sh
  source "${ROOT}/k8s/lib/kind-image.sh"
  kind_image_build "${GIDO_BUILD_PLATFORM}" "${TAG}" "${ROOT}/gido/backend" \
    --build-arg "PY_IMAGE=${PY_IMAGE:-python:3.11-slim}"
fi

kind load docker-image "${TAG}" --name "${KIND_CLUSTER_NAME}"

kubectl patch deployment gido-backend -n gido --type=strategic \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"backend","imagePullPolicy":"IfNotPresent"}]}}}}' \
  >/dev/null 2>&1 || true

kubectl rollout restart deployment/gido-backend -n gido
kubectl rollout status deployment/gido-backend -n gido --timeout=300s
kubectl get pods -n gido -l component=backend

echo ""
echo "完成: ${TAG} 已导入 Kind 并重启 backend"
