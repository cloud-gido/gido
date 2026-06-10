#!/usr/bin/env bash

# 一键：分层部署 GIDO 到 K3s（应用每次发版更新；Flink 运行时按需更新）
#
#   export KUBECONFIG=~/.kube/config-mac-orbstack
#   bash k8s/deploy-gido-k3s.sh
#
# 策略配置（推荐复制并修改）：
#   cp k8s/gido-deploy.env.example k8s/gido-deploy.env
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
# shellcheck source=lib/gido-deploy-image.sh
source "${ROOT}/k8s/lib/gido-deploy-image.sh"

gido_load_deploy_config "${ROOT}"
gido_require_app_build_or_allow

export GIDO_BUILD_PLATFORM="${GIDO_BUILD_PLATFORM:-$(gido_detect_build_platform)}"
APP_TAG="$(gido_resolve_app_tag)"
FLINK_TAG="$(gido_resolve_flink_tag)"
LOCAL_BACKEND="gido-backend:${APP_TAG}"
LOCAL_FRONTEND="gido-frontend:${APP_TAG}"
LOCAL_FLINK="gido-flink-sql-runner:${FLINK_TAG}"

BUILD_FLINK=0
if gido_should_build_flink "${ROOT}"; then
  BUILD_FLINK=1
fi
gido_deploy_print_plan "${ROOT}" "${APP_TAG}" "${FLINK_TAG}" "${BUILD_FLINK}"

CTX="$(${KUBECTL} config current-context 2>/dev/null || echo "")"
echo "==> GIDO 分层部署 context=${CTX} | 平台 ${GIDO_BUILD_PLATFORM}"

${KUBECTL} get nodes
${KUBECTL} get crd flinkdeployments.flink.apache.org >/dev/null 2>&1 || {
  echo "错误：未安装 Flink Kubernetes Operator CRD" >&2
  exit 1
}

echo "==> 确保 namespace flink / gido"
${KUBECTL} create ns flink --dry-run=client -o yaml | ${KUBECTL} apply -f -
${KUBECTL} create ns gido --dry-run=client -o yaml | ${KUBECTL} apply -f -
${KUBECTL} apply -f "${ROOT}/k8s/flink-operator-rbac.yaml"

if gido_should_build_app; then
  echo "==> 构建应用镜像（当前工作区 gido/backend + gido/frontend）"
  k3s_image_build "${GIDO_BUILD_PLATFORM}" "${LOCAL_BACKEND}" "${ROOT}/gido/backend"
  k3s_image_build "${GIDO_BUILD_PLATFORM}" "${LOCAL_FRONTEND}" "${ROOT}/gido/frontend" \
    --build-arg "NODE_IMAGE=${NODE_IMAGE:-docker.m.daocloud.io/library/node:18-alpine}" \
    --build-arg "NGINX_IMAGE=${NGINX_IMAGE:-docker.m.daocloud.io/library/nginx:alpine}"
else
  for img in "${LOCAL_BACKEND}" "${LOCAL_FRONTEND}"; do
    docker image inspect "${img}" >/dev/null 2>&1 || {
      echo "错误：跳过应用构建但本地无 ${img}" >&2
      exit 1
    }
  done
fi

if [[ "${BUILD_FLINK}" == "1" ]]; then
  echo "==> 构建 Flink 统一运行时（k8s/flink-sql-runner）"
  gido_flink_sql_runner_build "${GIDO_BUILD_PLATFORM}" "${LOCAL_FLINK}" "${ROOT}"
  gido_record_flink_runtime_hash "${ROOT}"
else
  gido_ensure_local_flink_image "${FLINK_TAG}"
fi

echo "==> registry 发布"
k3s_registry_apply_insecure_config "${ROOT}"
k3s_registry_ensure_node_registry
if ! k3s_registry_restart_k3s; then
  k3s_registry_log "继续 push（节点 pull 若失败请检查 registry 配置）"
fi
k3s_registry_wait_nodes_ready || true
k3s_registry_ensure_deployment "${ROOT}"

PUSH_LIST=( "${LOCAL_BACKEND}" "${LOCAL_FRONTEND}" )
if [[ "${BUILD_FLINK}" == "1" ]]; then
  PUSH_LIST+=( "${LOCAL_FLINK}" )
fi
k3s_registry_push_images "${GIDO_REGISTRY_PF_PORT:-5001}" "${PUSH_LIST[@]}"

export GIDO_BACKEND_IMAGE="$(k3s_registry_image_ref "gido-backend" "${APP_TAG}")"
export GIDO_FRONTEND_IMAGE="$(k3s_registry_image_ref "gido-frontend" "${APP_TAG}")"
export GIDO_FLINK_OPERATOR_IMAGE="$(k3s_registry_image_ref "gido-flink-sql-runner" "${FLINK_TAG}")"
echo "    backend:  ${GIDO_BACKEND_IMAGE}"
echo "    frontend: ${GIDO_FRONTEND_IMAGE}"
echo "    flink:    ${GIDO_FLINK_OPERATOR_IMAGE}"

k3s_registry_verify_node_pull "${GIDO_BACKEND_IMAGE}" || true
if [[ "${BUILD_FLINK}" == "1" ]]; then
  k3s_registry_verify_node_pull "${GIDO_FLINK_OPERATOR_IMAGE}" || true
fi

gido_record_deploy_tags "${ROOT}" "${APP_TAG}" "${FLINK_TAG}"

echo "==> apply gido stack（应用 pull=${GIDO_DEPLOY_APP_PULL}）"
export GIDO_SKIP_BUILD=1
bash "${ROOT}/k8s/apply-gido-stack.sh"

echo ""
echo "完成。"
echo "  应用 tag:   ${APP_TAG}（每次发版更新）"
echo "  Flink tag:  ${FLINK_TAG}$([[ "${BUILD_FLINK}" == "1" ]] && echo '（本次已重建）' || echo '（未重建，复用稳定运行时）')"
${KUBECTL} -n gido get pods
echo ""
if [[ "${BUILD_FLINK}" != "1" ]]; then
  echo "提示：Flink 运行时未变；已跑 SQL/JAR 作业无需重建。改 connector/SqlRunner 后会 auto 重建。"
fi
echo "访问: http://127.0.0.1:8080 | admin / admin123"

# shellcheck source=lib/gido-port-forward.sh
source "${ROOT}/k8s/lib/gido-port-forward.sh"
gido_pf_maybe_start
