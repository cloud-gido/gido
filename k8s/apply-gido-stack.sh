#!/usr/bin/env bash
# 一键：构建 GIDO 镜像并部署到当前 kubectl 上下文（与 k8s/flink.yaml 同集群）。
#
# 用法：
#   bash k8s/apply-gido-stack.sh
#
# 环境变量（可选）：
#   GIDO_BACKEND_IMAGE   默认 gido-backend:latest
#   GIDO_FRONTEND_IMAGE  默认 gido-frontend:latest
#   GIDO_APPLY_FLINK=1   先 kubectl apply k8s/flink.yaml
#   GIDO_KIND_LOAD=1     再 kind load docker-image（KIND_CLUSTER_NAME 默认 kind）
#   GIDO_APPLY_INGRESS=1 再 kubectl apply k8s/gido-ingress.yaml
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_IMAGE="${GIDO_BACKEND_IMAGE:-gido-backend:latest}"
FRONTEND_IMAGE="${GIDO_FRONTEND_IMAGE:-gido-frontend:latest}"

echo "==> build images"
docker build -t "${BACKEND_IMAGE}" "${ROOT}/gido/backend"
docker build -t "${FRONTEND_IMAGE}" "${ROOT}/gido/frontend"

if [[ "${GIDO_KIND_LOAD:-}" == "1" ]]; then
  KIND_NAME="${KIND_CLUSTER_NAME:-kind}"
  echo "==> kind load docker-image -> ${KIND_NAME}"
  kind load docker-image "${BACKEND_IMAGE}" --name "${KIND_NAME}"
  kind load docker-image "${FRONTEND_IMAGE}" --name "${KIND_NAME}"
fi

if [[ "${GIDO_APPLY_FLINK:-}" == "1" ]]; then
  echo "==> kubectl apply flink"
  kubectl apply -f "${ROOT}/k8s/flink.yaml"
fi

echo "==> kubectl apply gido (sed image placeholders)"
sed \
  -e "s#__BACKEND_IMAGE__#${BACKEND_IMAGE}#g" \
  -e "s#__FRONTEND_IMAGE__#${FRONTEND_IMAGE}#g" \
  "${ROOT}/k8s/gido.yaml" | kubectl apply -f -

echo "==> wait rollout"
kubectl rollout status deployment/mysql -n gido --timeout=300s
kubectl rollout status deployment/redis -n gido --timeout=120s
kubectl rollout status deployment/gido-backend -n gido --timeout=300s
kubectl rollout status deployment/gido-frontend -n gido --timeout=180s

if [[ "${GIDO_APPLY_INGRESS:-}" == "1" ]]; then
  echo "==> kubectl apply ingress"
  kubectl apply -f "${ROOT}/k8s/gido-ingress.yaml"
fi

echo ""
echo "完成。访问方式二选一："
echo "  A) kubectl port-forward -n gido svc/frontend 8080:80   浏览器 http://127.0.0.1:8080"
echo "  B) 已 apply Ingress 时：http://gido.localhost（hosts 指向 Ingress IP）"
echo "登录 admin / admin123（见 ConfigMap GIDO_BOOTSTRAP_ADMIN_PASSWORD，生产请改 Secret 与密码）"
