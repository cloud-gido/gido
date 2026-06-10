#!/usr/bin/env bash
# 准生产（短期）：Mac 本机构建 → push 集群 HTTP registry → 重启 K3s → 节点 pull → 部署 GIDO
#
# 用法：
#   export KUBECONFIG=~/.kube/config-mac-orbstack
#   export K3S_SSH_HOST=<真实可 SSH 的 K3s 节点>  # 可选；OrbStack 勿用 API 地址 192.168.1.68
#   export K3S_SSH_USER=infras                  # 可选
#   GIDO_K3S_SKIP_RESTART=1                     # 已在 ubuntu 手动 restart 过时用
#   bash k8s/apply-gido-k3s-registry.sh
#
# 仅重新 push + rollout（跳过构建）：
#   GIDO_SKIP_BUILD=1 bash k8s/apply-gido-k3s-registry.sh
#
# 已手动 restart k3s，跳过 SSH 重启：
#   GIDO_K3S_SKIP_RESTART=1 bash k8s/apply-gido-k3s-registry.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config-mac-orbstack}"
export GIDO_KIND_LOAD=0
export GIDO_K3S_USE_REGISTRY=1
export KUBECTL="${KUBECTL:-kubectl}"

# shellcheck source=lib/kind-image.sh
source "${ROOT}/k8s/lib/kind-image.sh"
# shellcheck source=lib/k3s-image.sh
source "${ROOT}/k8s/lib/k3s-image.sh"
# shellcheck source=lib/k3s-registry.sh
source "${ROOT}/k8s/lib/k3s-registry.sh"
# shellcheck source=lib/flink-sql-runner-image.sh
source "${ROOT}/k8s/lib/flink-sql-runner-image.sh"

export GIDO_BUILD_PLATFORM="${GIDO_BUILD_PLATFORM:-$(gido_detect_build_platform)}"
IMAGE_TAG="${GIDO_IMAGE_TAG:-orbstack}"
LOCAL_BACKEND="gido-backend:${IMAGE_TAG}"
LOCAL_FRONTEND="gido-frontend:${IMAGE_TAG}"
LOCAL_FLINK="gido-flink-sql-runner:${IMAGE_TAG}"

CTX="$(${KUBECTL} config current-context 2>/dev/null || echo "")"
echo "==> 准生产 registry 部署 context=${CTX}"
echo "==> 平台 ${GIDO_BUILD_PLATFORM}（$(gido_detect_build_platform_source)）| 标签 ${IMAGE_TAG}"

${KUBECTL} get nodes
${KUBECTL} get crd flinkdeployments.flink.apache.org >/dev/null 2>&1 || {
  echo "错误：未安装 Flink Kubernetes Operator CRD" >&2
  exit 1
}

echo "==> 确保 namespace flink / gido"
${KUBECTL} create ns flink --dry-run=client -o yaml | ${KUBECTL} apply -f -
${KUBECTL} create ns gido --dry-run=client -o yaml | ${KUBECTL} apply -f -
${KUBECTL} apply -f "${ROOT}/k8s/flink-operator-rbac.yaml"

if [[ "${GIDO_SKIP_BUILD:-}" != "1" ]]; then
  echo "==> 本机构建镜像（backend + frontend + gido-flink-runtime / gido-flink-sql-runner）"
  k3s_image_build "${GIDO_BUILD_PLATFORM}" "${LOCAL_BACKEND}" "${ROOT}/gido/backend"
  k3s_image_build "${GIDO_BUILD_PLATFORM}" "${LOCAL_FRONTEND}" "${ROOT}/gido/frontend" \
    --build-arg "NODE_IMAGE=${NODE_IMAGE:-docker.m.daocloud.io/library/node:18-alpine}" \
    --build-arg "NGINX_IMAGE=${NGINX_IMAGE:-docker.m.daocloud.io/library/nginx:alpine}"
  gido_flink_sql_runner_build "${GIDO_BUILD_PLATFORM}" "${LOCAL_FLINK}" "${ROOT}"
else
  echo "==> 跳过构建（GIDO_SKIP_BUILD=1）"
  for img in "${LOCAL_BACKEND}" "${LOCAL_FRONTEND}" "${LOCAL_FLINK}"; do
    docker image inspect "${img}" >/dev/null 2>&1 || {
      echo "错误：本地无 ${img}" >&2
      exit 1
    }
  done
fi

echo "==> registry 发布（配置 mirrors → restart k3s → push → 试拉）"
k3s_registry_publish_images "${ROOT}" "${LOCAL_BACKEND}" "${LOCAL_FRONTEND}" "${IMAGE_TAG}" "${LOCAL_FLINK}"
export GIDO_BACKEND_IMAGE="$(k3s_registry_image_ref "gido-backend" "${IMAGE_TAG}")"
export GIDO_FRONTEND_IMAGE="$(k3s_registry_image_ref "gido-frontend" "${IMAGE_TAG}")"
export GIDO_FLINK_OPERATOR_IMAGE="$(k3s_registry_image_ref "gido-flink-sql-runner" "${IMAGE_TAG}")"
echo "    backend 镜像: ${GIDO_BACKEND_IMAGE}"
echo "    frontend 镜像: ${GIDO_FRONTEND_IMAGE}"
echo "    flink 镜像:   ${GIDO_FLINK_OPERATOR_IMAGE}"

echo "==> apply gido stack"
export GIDO_SKIP_BUILD=1
bash "${ROOT}/k8s/apply-gido-stack.sh"

k3s_registry_rollout_gido

echo ""
echo "完成（registry 准生产路径）。"
${KUBECTL} -n gido get pods
echo ""
echo "Flink Operator 镜像（JAR + SQL Operator 共用）: ${GIDO_FLINK_OPERATOR_IMAGE}"
echo "  SQL Runner: local:///opt/flink/usrlib/sql-runner.jar"
echo "  访问 GIDO: 见上方 port-forward 输出（本机/局域网 :8080）"
echo "  登录: admin / admin123"

# shellcheck source=lib/gido-port-forward.sh
source "${ROOT}/k8s/lib/gido-port-forward.sh"
gido_pf_maybe_start
