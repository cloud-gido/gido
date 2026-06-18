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
exec bash "${ROOT}/k8s/apply-gido-stack.sh"
