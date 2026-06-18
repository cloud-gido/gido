#!/usr/bin/env bash
set -euo pipefail
export PATH="${HOME}/.local/bin:/c/Windows/System32:${PATH}"
# Git Bash on Windows: py launcher for render_build_context.py
if ! command -v python3 >/dev/null 2>&1 && command -v py >/dev/null 2>&1; then
  python3() { py -3 "$@"; }
  export -f python3
fi
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/kind-gido.yaml}"
export KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-gido}"
export GIDO_BUILD_PLATFORM="${GIDO_BUILD_PLATFORM:-linux/amd64}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 可选覆盖：k8s/kind-local.env（gitignore，见 kind-local.env.example）
if [[ -f "${ROOT}/k8s/kind-local.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/k8s/kind-local.env"
fi

export GIDO_FLINK_OPERATOR_IMAGE="${GIDO_FLINK_OPERATOR_IMAGE:-ghcr.io/cloud-gido/gido/dev-1/gido-flink-runtime:dev-1-5b99f2d}"
export GIDO_SKIP_FLINK_BUILD="${GIDO_SKIP_FLINK_BUILD:-1}"

# Kind 节点往往无法直连 ghcr.io；部署前先导入运行时镜像
bash "${ROOT}/k8s/kind-load-flink-runtime.sh" "${GIDO_FLINK_OPERATOR_IMAGE}"

exec bash "${ROOT}/k8s/apply-gido-stack.sh"
