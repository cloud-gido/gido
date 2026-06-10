#!/usr/bin/env bash
# 部署 GIDO 到局域网 OrbStack / K3s 集群（kubeconfig: ~/.kube/config-mac-orbstack）
#
# 本机导入（默认）：Docker 构建 → k3s ctr import，与 Kind 相同。
# 准生产 registry：请用 k8s/apply-gido-k3s-registry.sh（或 GIDO_K3S_USE_REGISTRY=1 转调该脚本）。
#
# 用法：
#   export KUBECONFIG=~/.kube/config-mac-orbstack
#   bash k8s/apply-gido-orbstack.sh
#   bash k8s/apply-gido-k3s-registry.sh    # 本机 build → push registry → restart k3s → deploy
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${GIDO_K3S_USE_REGISTRY:-}" == "1" ]]; then
  exec bash "${ROOT}/k8s/apply-gido-k3s-registry.sh" "$@"
fi

export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config-mac-orbstack}"
export GIDO_KIND_LOAD=0
export KUBECTL="${KUBECTL:-kubectl}"

# shellcheck source=lib/kind-image.sh
source "${ROOT}/k8s/lib/kind-image.sh"
# shellcheck source=lib/k3s-image.sh
source "${ROOT}/k8s/lib/k3s-image.sh"
# shellcheck source=lib/flink-sql-runner-image.sh
source "${ROOT}/k8s/lib/flink-sql-runner-image.sh"
export GIDO_BUILD_PLATFORM="${GIDO_BUILD_PLATFORM:-$(gido_detect_build_platform)}"

IMAGE_TAG="${GIDO_IMAGE_TAG:-orbstack}"
LOCAL_BACKEND="gido-backend:${IMAGE_TAG}"
LOCAL_FRONTEND="gido-frontend:${IMAGE_TAG}"
LOCAL_FLINK="gido-flink-sql-runner:${IMAGE_TAG}"

CTX="$(${KUBECTL} config current-context 2>/dev/null || echo "")"
echo "==> 目标集群 context=${CTX} KUBECONFIG=${KUBECONFIG}"
echo "==> 镜像模式: 本机导入 containerd（与 Kind 相同）"

${KUBECTL} get nodes
${KUBECTL} get crd flinkdeployments.flink.apache.org >/dev/null 2>&1 || {
  echo "错误：未安装 Flink Kubernetes Operator CRD" >&2
  exit 1
}

echo "==> 确保 namespace flink / gido"
${KUBECTL} create ns flink --dry-run=client -o yaml | ${KUBECTL} apply -f -
${KUBECTL} create ns gido --dry-run=client -o yaml | ${KUBECTL} apply -f -

echo "==> apply flink-operator-rbac（若已存在会跳过冲突）"
${KUBECTL} apply -f "${ROOT}/k8s/flink-operator-rbac.yaml"

echo "==> 构建 GIDO 镜像（${GIDO_BUILD_PLATFORM}，来源: $(gido_detect_build_platform_source)）"
k3s_image_build "${GIDO_BUILD_PLATFORM}" "${LOCAL_BACKEND}" "${ROOT}/gido/backend"
k3s_image_build "${GIDO_BUILD_PLATFORM}" "${LOCAL_FRONTEND}" "${ROOT}/gido/frontend" \
  --build-arg "NODE_IMAGE=${NODE_IMAGE:-docker.m.daocloud.io/library/node:18-alpine}" \
  --build-arg "NGINX_IMAGE=${NGINX_IMAGE:-docker.m.daocloud.io/library/nginx:alpine}"
gido_flink_sql_runner_build "${GIDO_BUILD_PLATFORM}" "${LOCAL_FLINK}" "${ROOT}"

echo "==> [本机导入] Docker → K3s containerd"
if ! k3s_ctr_import_cmd >/dev/null 2>&1 && [[ -z "${K3S_SSH_HOST:-}" ]]; then
  K3S_API="$(${KUBECTL} config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null || true)"
  if [[ "${K3S_API}" =~ https?://([^:/]+) ]]; then
    export K3S_SSH_HOST="${K3S_SSH_HOST:-${BASH_REMATCH[1]}}"
    echo "    提示：将尝试 SSH 导入到 ${K3S_SSH_HOST}（可 export K3S_SSH_USER=...）"
  fi
fi
k3s_image_import_to_cluster "${LOCAL_BACKEND}"
k3s_image_import_to_cluster "${LOCAL_FRONTEND}"
k3s_image_import_to_cluster "${LOCAL_FLINK}"
export GIDO_BACKEND_IMAGE="${LOCAL_BACKEND}"
export GIDO_FRONTEND_IMAGE="${LOCAL_FRONTEND}"
export GIDO_FLINK_OPERATOR_IMAGE="${LOCAL_FLINK}"

echo "==> apply gido stack"
export GIDO_SKIP_BUILD=1
bash "${ROOT}/k8s/apply-gido-stack.sh"

echo ""
echo "完成（OrbStack / 局域网 K3s）。"
echo "  export KUBECONFIG=~/.kube/config-mac-orbstack"
echo "  kubectl -n gido port-forward svc/frontend 8080:80"
echo "  浏览器 http://127.0.0.1:8080  登录 admin / admin123"
echo "  准生产 registry 路径: bash k8s/apply-gido-k3s-registry.sh"
