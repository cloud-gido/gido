#!/usr/bin/env bash

# 一键部署 GIDO 到 K3s（分层策略，见 k8s/gido-deploy.env.example）
#
#   cp k8s/gido-deploy.env.example k8s/gido-deploy.env   # 首次
#   export KUBECONFIG=~/.kube/config-mac-orbstack
#   bash k8s/deploy-gido-k3s.sh
#
# 只改了 GIDO 前后端 → 自动构建应用 + 新 app tag，Flink 运行时不动
# 改了 k8s/flink-sql-runner → auto 检测 hash 后重建 flink-runtime
# 强制重建 Flink：GIDO_FORCE_REBUILD_FLINK=1 bash k8s/deploy-gido-k3s.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config-mac-orbstack}"

echo "==> GIDO 一键部署（分层：应用必更 / Flink 按需）"
echo "    配置: k8s/gido-deploy.env（无则用 example 默认）"
echo "    KUBECONFIG=${KUBECONFIG}"

exec bash "${ROOT}/k8s/apply-gido-k3s-registry.sh"
