#!/usr/bin/env bash
# 一键：构建 GIDO 镜像并部署最小栈（PostgreSQL + backend + frontend）到当前 kubectl 上下文。
#
# 用法：
#   bash k8s/apply-gido-stack.sh
#   KIND_CLUSTER_NAME=gido bash k8s/apply-gido-stack.sh          # 自动检测 Kind 并导入镜像
#
# 环境变量（可选）：
#   GIDO_BACKEND_IMAGE   默认 gido-backend:latest
#   GIDO_FRONTEND_IMAGE  默认 gido-frontend:latest
#   GIDO_APPLY_FLINK=1   可选：额外 apply k8s/flink.yaml（Session Flink，一般不需要）
#   GIDO_KIND_LOAD=0     强制不导入 Kind（非 Kind 集群时用）
#   GIDO_APPLY_INGRESS=1 再 kubectl apply k8s/gido-ingress.yaml
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/kind-image.sh
source "${ROOT}/k8s/lib/kind-image.sh"
# shellcheck source=lib/flink-sql-runner-image.sh
source "${ROOT}/k8s/lib/flink-sql-runner-image.sh"
# shellcheck source=lib/gido-port-forward.sh
source "${ROOT}/k8s/lib/gido-port-forward.sh"

BACKEND_IMAGE="${GIDO_BACKEND_IMAGE:-gido-backend:latest}"
FRONTEND_IMAGE="${GIDO_FRONTEND_IMAGE:-gido-frontend:latest}"
FLINK_OPERATOR_IMAGE="${GIDO_FLINK_OPERATOR_IMAGE:-$(gido_flink_sql_runner_default_tag)}"
KIND_NAME="${KIND_CLUSTER_NAME:-$(kind get clusters 2>/dev/null | head -1 || echo "")}"
BUILD_PLATFORM="${GIDO_BUILD_PLATFORM:-$(gido_detect_build_platform)}"
KUBECTL="${KUBECTL:-kubectl}"
CTX="$(${KUBECTL} config current-context 2>/dev/null || echo "")"

_use_kind_load() {
  if [[ "${GIDO_KIND_LOAD:-}" == "0" ]]; then
    return 1
  fi
  if [[ "${GIDO_KIND_LOAD:-}" == "1" ]]; then
    return 0
  fi
  # 当前 context 为 kind-* 时自动导入
  [[ "${CTX}" == kind-* ]] && [[ -n "${KIND_NAME}" ]]
}

if [[ "${GIDO_SKIP_BUILD:-}" != "1" ]]; then
  echo "==> build images (${BUILD_PLATFORM}，单架构，供 Kind/生产节点)"
  kind_image_build "${BUILD_PLATFORM}" "${BACKEND_IMAGE}" "${ROOT}/gido/backend"
  kind_image_build "${BUILD_PLATFORM}" "${FRONTEND_IMAGE}" "${ROOT}/gido/frontend" \
    --build-arg "NODE_IMAGE=${NODE_IMAGE:-docker.m.daocloud.io/library/node:18-alpine}" \
    --build-arg "NGINX_IMAGE=${NGINX_IMAGE:-docker.m.daocloud.io/library/nginx:alpine}"
  gido_flink_sql_runner_build "${BUILD_PLATFORM}" "${FLINK_OPERATOR_IMAGE}" "${ROOT}"
else
  echo "==> 跳过构建（GIDO_SKIP_BUILD=1，使用已有镜像 ${BACKEND_IMAGE} / ${FRONTEND_IMAGE} / ${FLINK_OPERATOR_IMAGE}）"
fi

if _use_kind_load; then
  echo "==> Kind 导入镜像 -> ${KIND_NAME} (context=${CTX})"
  bash "${ROOT}/k8s/kind-load-mirror-images.sh" "${BACKEND_IMAGE}" "${FRONTEND_IMAGE}" "${FLINK_OPERATOR_IMAGE}"
fi

if [[ "${GIDO_APPLY_FLINK:-}" == "1" ]]; then
  echo "==> kubectl apply flink (Session，可选)"
  ${KUBECTL} apply -f "${ROOT}/k8s/flink.yaml"
fi

echo "==> kubectl apply gido (sed image placeholders)"
sed \
  -e "s#__BACKEND_IMAGE__#${BACKEND_IMAGE}#g" \
  -e "s#__FRONTEND_IMAGE__#${FRONTEND_IMAGE}#g" \
  -e "s#__FLINK_OPERATOR_IMAGE__#${FLINK_OPERATOR_IMAGE}#g" \
  "${ROOT}/k8s/gido.yaml" | ${KUBECTL} apply -f -

if ${KUBECTL} get ns flink >/dev/null 2>&1; then
  echo "==> kubectl apply flink-operator-rbac"
  ${KUBECTL} apply -f "${ROOT}/k8s/flink-operator-rbac.yaml"
fi

echo "==> wait rollout"
${KUBECTL} rollout status deployment/postgres -n gido --timeout=300s
${KUBECTL} rollout status deployment/gido-backend -n gido --timeout=300s
${KUBECTL} rollout status deployment/gido-frontend -n gido --timeout=180s

# 清理卡在 CreateContainerError / ImagePull 的孤立 Pod（Recreate 策略下不应出现，兜底）
${KUBECTL} delete pod -n gido --field-selector=status.phase=Failed --ignore-not-found 2>/dev/null || true
for p in $(${KUBECTL} get pods -n gido -o jsonpath='{range .items[?(@.status.containerStatuses[0].state.waiting.reason=="CreateContainerError")]}{.metadata.name}{" "}{end}' 2>/dev/null); do
  [[ -n "${p}" ]] && ${KUBECTL} delete pod -n gido "${p}" --ignore-not-found || true
done

if [[ "${GIDO_APPLY_INGRESS:-}" == "1" ]]; then
  echo "==> kubectl apply ingress"
  ${KUBECTL} apply -f "${ROOT}/k8s/gido-ingress.yaml"
fi

gido_pf_maybe_start

echo ""
echo "完成。登录 admin / admin123（生产请改 Secret）"
