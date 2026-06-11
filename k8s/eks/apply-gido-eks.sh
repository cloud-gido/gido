#!/usr/bin/env bash
# 将 gido-eks-external-pg.yaml 占位符替换后 apply。
#
# GHCR（推荐，push dev 后 CI 自动打镜像）：
#   export GIDO_USE_GHCR=1
#   export GIDO_GHCR_REPO=ghcr.io/cloud-gido/gido
#   export GIDO_EKS_IMAGE_TAG=dev
#   export GIDO_EKS_S3_BUCKET=...
#   ... 其余见下方
#   bash k8s/eks/apply-gido-eks.sh
#
# ECR：
#   export GIDO_USE_GHCR=0
#   export GIDO_EKS_ACCOUNT=066158985613
#   export GIDO_EKS_REGION=ap-northeast-1
#   export GIDO_EKS_IMAGE_TAG=v1.0.0
#
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="${DIR}/gido-eks-external-pg.yaml"
KUBECTL="${KUBECTL:-kubectl}"

GIDO_USE_GHCR="${GIDO_USE_GHCR:-1}"
GIDO_GHCR_REPO="${GIDO_GHCR_REPO:-ghcr.io/cloud-gido/gido}"
GIDO_EKS_NAMESPACE="${GIDO_EKS_NAMESPACE:-gido}"
GIDO_EKS_FLINK_NAMESPACE="${GIDO_EKS_FLINK_NAMESPACE:-flink}"

_required() {
  local name="$1" val="${!1:-}"
  if [[ -z "${val}" ]]; then
    echo "错误：请设置环境变量 ${name}" >&2
    exit 1
  fi
}

_required GIDO_EKS_IMAGE_TAG
_required GIDO_EKS_S3_BUCKET
_required GIDO_EKS_RDS_HOST
_required GIDO_EKS_DB_USER
_required GIDO_EKS_DB_PASSWORD
_required GIDO_EKS_DB_NAME
_required GIDO_EKS_SECRET_KEY
_required GIDO_EKS_ARTIFACT_TOKEN
_required GIDO_EKS_BACKEND_IRSA

# 无 Ingress 时用占位域名（port-forward 访问不受影响）
GIDO_EKS_INGRESS_HOST="${GIDO_EKS_INGRESS_HOST:-gido.local}"

if [[ "${GIDO_USE_GHCR}" != "1" ]]; then
  _required GIDO_EKS_ACCOUNT
  _required GIDO_EKS_REGION
fi

GIDO_EKS_ACCOUNT="${GIDO_EKS_ACCOUNT:-000000000000}"
GIDO_EKS_REGION="${GIDO_EKS_REGION:-us-east-1}"

if [[ "${GIDO_USE_GHCR}" == "1" ]]; then
  BACKEND_IMAGE="${GIDO_GHCR_REPO}/gido-backend:${GIDO_EKS_IMAGE_TAG}"
  FRONTEND_IMAGE="${GIDO_GHCR_REPO}/gido-frontend:${GIDO_EKS_IMAGE_TAG}"
  FLINK_RUNTIME_IMAGE="${GIDO_GHCR_REPO}/gido-flink-runtime:${GIDO_EKS_IMAGE_TAG}"
else
  REG="${GIDO_EKS_ACCOUNT}.dkr.ecr.${GIDO_EKS_REGION}.amazonaws.com"
  BACKEND_IMAGE="${REG}/gido-backend:${GIDO_EKS_IMAGE_TAG}"
  FRONTEND_IMAGE="${REG}/gido-frontend:${GIDO_EKS_IMAGE_TAG}"
  FLINK_RUNTIME_IMAGE="${REG}/gido-flink-runtime:${GIDO_EKS_IMAGE_TAG}"
fi

DB_PASSWORD_ENC="$(python3 -c "import urllib.parse; print(urllib.parse.quote('''${GIDO_EKS_DB_PASSWORD}''', safe=''))")"

echo "==> apply GIDO EKS stack (external RDS)"
echo "    backend:  ${BACKEND_IMAGE}"
echo "    frontend: ${FRONTEND_IMAGE}"
echo "    flink:    ${FLINK_RUNTIME_IMAGE}"

sed \
  -e "s#CHANGE_ME_AWS_ACCOUNT#${GIDO_EKS_ACCOUNT}#g" \
  -e "s#CHANGE_ME_AWS_REGION#${GIDO_EKS_REGION}#g" \
  -e "s#CHANGE_ME_ECR_TAG#${GIDO_EKS_IMAGE_TAG}#g" \
  -e "s#CHANGE_ME_S3_BUCKET#${GIDO_EKS_S3_BUCKET}#g" \
  -e "s#CHANGE_ME_RDS_HOST#${GIDO_EKS_RDS_HOST}#g" \
  -e "s#CHANGE_ME_DB_USER#${GIDO_EKS_DB_USER}#g" \
  -e "s#CHANGE_ME_DB_PASSWORD#${DB_PASSWORD_ENC}#g" \
  -e "s#CHANGE_ME_DB_NAME#${GIDO_EKS_DB_NAME}#g" \
  -e "s#CHANGE_ME_SECRET_KEY#${GIDO_EKS_SECRET_KEY}#g" \
  -e "s#CHANGE_ME_ARTIFACT_TOKEN#${GIDO_EKS_ARTIFACT_TOKEN}#g" \
  -e "s#CHANGE_ME_BACKEND_IRSA#${GIDO_EKS_BACKEND_IRSA}#g" \
  -e "s#CHANGE_ME_INGRESS_HOST#${GIDO_EKS_INGRESS_HOST}#g" \
  -e "s#CHANGE_ME_GIDO_NAMESPACE#${GIDO_EKS_NAMESPACE}#g" \
  -e "s#CHANGE_ME_BACKEND_IMAGE#${BACKEND_IMAGE}#g" \
  -e "s#CHANGE_ME_FRONTEND_IMAGE#${FRONTEND_IMAGE}#g" \
  -e "s#CHANGE_ME_FLINK_RUNTIME_IMAGE#${FLINK_RUNTIME_IMAGE}#g" \
  "${MANIFEST}" | ${KUBECTL} apply -f -

echo "==> wait rollout"
${KUBECTL} rollout status deployment/gido-backend -n "${GIDO_EKS_NAMESPACE}" --timeout=300s
${KUBECTL} rollout status deployment/gido-frontend -n "${GIDO_EKS_NAMESPACE}" --timeout=180s

echo ""
echo "完成。首次部署请初始化元库表："
echo "  kubectl -n ${GIDO_EKS_NAMESPACE} exec deploy/gido-backend -- python init_db.py"
echo ""
echo "Flink runtime 自检（可选）："
echo "  bash k8s/flink-sql-runner/verify-image.sh ${FLINK_RUNTIME_IMAGE}"
echo ""
echo "访问: https://${GIDO_EKS_INGRESS_HOST}  或  kubectl -n ${GIDO_EKS_NAMESPACE} port-forward svc/frontend 8080:80"
echo "默认账号 admin / admin123（登录后务必改密）"
echo ""
echo "部署清单详见: k8s/eks/DEPLOY-GIDO.md"
