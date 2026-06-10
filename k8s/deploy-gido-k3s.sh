#!/usr/bin/env bash
# 一键部署 GIDO 到远程 K3s（含统一 Flink 运行时 gido-flink-runtime + Operator 路径 + port-forward）
#
#   export KUBECONFIG=~/.kube/config-mac-orbstack
#   bash k8s/deploy-gido-k3s.sh
#
# 节点已配好 registry mirrors、不想每次 restart k3s：
#   GIDO_K3S_SKIP_RESTART=1 bash k8s/deploy-gido-k3s.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config-mac-orbstack}"

echo "==> GIDO 一键部署（K3s registry + gido-flink-runtime + Flink Operator + port-forward）"
echo "    KUBECONFIG=${KUBECONFIG}"

exec bash "${ROOT}/k8s/apply-gido-k3s-registry.sh"
