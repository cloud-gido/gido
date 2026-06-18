#!/usr/bin/env bash
# 将 GHCR / 远程 Flink 运行时预拉并导入 Kind（避免节点直连 ghcr.io TLS 超时）
#
#   bash k8s/kind-load-flink-runtime.sh
#   bash k8s/kind-load-flink-runtime.sh ghcr.io/cloud-gido/gido/dev-1/gido-flink-runtime:dev-1-5b99f2d
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/kind-gido.yaml}"
KIND_NAME="${KIND_CLUSTER_NAME:-gido}"

if [[ -f "${ROOT}/k8s/kind-local.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/k8s/kind-local.env"
fi

IMAGE="${1:-${GIDO_FLINK_OPERATOR_IMAGE:-ghcr.io/cloud-gido/gido/dev-1/gido-flink-runtime:dev-1-5b99f2d}}"

echo "==> docker pull ${IMAGE}"
docker pull "${IMAGE}"

echo "==> kind load docker-image ${IMAGE} -> ${KIND_NAME}"
kind load docker-image "${IMAGE}" --name "${KIND_NAME}"

echo "==> 节点镜像列表（节选）"
docker exec "${KIND_NAME}-control-plane" crictl images 2>/dev/null | grep -F "${IMAGE##*/}" || true

echo ""
echo "完成。若已有 ImagePullBackOff 的 Flink Pod，删除后重建即可："
echo "  kubectl delete pod -n flink -l app=<deployment-name>"
