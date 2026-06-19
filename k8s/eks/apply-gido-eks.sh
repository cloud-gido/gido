#!/usr/bin/env bash
# 将 gido-eks-external-pg.yaml 占位符替换后 apply。
#
# GHCR（推荐）：
#   export GIDO_USE_GHCR=1
#   export GIDO_GHCR_REPO=ghcr.io/cloud-gido/gido
#   export GIDO_EKS_IMAGE_TAG=dev-1
#   export GIDO_EKS_S3_BUCKET=flink-on-devtest
#   export GIDO_EKS_S3_JAR_PREFIX=gido-flink
#   export GIDO_EKS_REGION=ap-southeast-1
#   export GIDO_EKS_S3_ENDPOINT_URL=https://s3.ap-southeast-1.amazonaws.com
#   export GIDO_EKS_RDS_HOST=xxx.rds.amazonaws.com
#   export GIDO_EKS_DB_USER=gido
#   export GIDO_EKS_DB_PASSWORD='...'
#   export GIDO_EKS_DB_NAME=gido
#   export GIDO_EKS_SECRET_KEY=...
#   export GIDO_EKS_ARTIFACT_TOKEN=...
#   export GIDO_EKS_ADMIN_PASSWORD=...
#   export GIDO_EKS_BACKEND_IRSA=arn:aws:iam::...
#   bash k8s/eks/apply-gido-eks.sh
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

GIDO_EKS_ADMIN_PASSWORD="${GIDO_EKS_ADMIN_PASSWORD:-ChangeMeAfterLogin123!}"
GIDO_EKS_INGRESS_HOST="${GIDO_EKS_INGRESS_HOST:-gido.local}"
GIDO_EKS_S3_JAR_PREFIX="${GIDO_EKS_S3_JAR_PREFIX:-gido-flink}"

if [[ "${GIDO_USE_GHCR}" != "1" ]]; then
  _required GIDO_EKS_ACCOUNT
  _required GIDO_EKS_REGION
fi

GIDO_EKS_ACCOUNT="${GIDO_EKS_ACCOUNT:-000000000000}"
GIDO_EKS_REGION="${GIDO_EKS_REGION:-ap-southeast-1}"
GIDO_EKS_S3_ENDPOINT_URL="${GIDO_EKS_S3_ENDPOINT_URL:-https://s3.${GIDO_EKS_REGION}.amazonaws.com}"

if [[ "${GIDO_USE_GHCR}" == "1" ]]; then
  BACKEND_IMAGE="${GIDO_GHCR_REPO}/gido-backend:${GIDO_EKS_IMAGE_TAG}"
  FRONTEND_IMAGE="${GIDO_GHCR_REPO}/gido-frontend:${GIDO_EKS_IMAGE_TAG}"
  FLINK_RUNTIME_IMAGE="${GIDO_GHCR_REPO}/gido-flink-runtime:${GIDO_EKS_IMAGE_TAG}"
fi

if [[ "${GIDO_USE_GHCR}" != "1" ]]; then
  REG="${GIDO_EKS_ACCOUNT}.dkr.ecr.${GIDO_EKS_REGION}.amazonaws.com"
  BACKEND_IMAGE="${REG}/gido-backend:${GIDO_EKS_IMAGE_TAG}"
  FRONTEND_IMAGE="${REG}/gido-frontend:${GIDO_EKS_IMAGE_TAG}"
  FLINK_RUNTIME_IMAGE="${REG}/gido-flink-runtime:${GIDO_EKS_IMAGE_TAG}"
fi

echo "==> apply GIDO EKS stack (external RDS)"
echo "    backend:  ${BACKEND_IMAGE}"
echo "    frontend: ${FRONTEND_IMAGE}"
echo "    flink:    ${FLINK_RUNTIME_IMAGE}"
echo "    rds:      ${GIDO_EKS_RDS_HOST}:5432/${GIDO_EKS_DB_NAME}"
echo "    s3 jar:   s3://${GIDO_EKS_S3_BUCKET}/${GIDO_EKS_S3_JAR_PREFIX}"

sed \
  -e "s#CHANGE_ME_AWS_ACCOUNT#${GIDO_EKS_ACCOUNT}#g" \
  -e "s#CHANGE_ME_AWS_REGION#${GIDO_EKS_REGION}#g" \
  -e "s#CHANGE_ME_ECR_TAG#${GIDO_EKS_IMAGE_TAG}#g" \
  -e "s#CHANGE_ME_S3_BUCKET#${GIDO_EKS_S3_BUCKET}#g" \
  -e "s#CHANGE_ME_S3_JAR_PREFIX#${GIDO_EKS_S3_JAR_PREFIX}#g" \
  -e "s#CHANGE_ME_S3_ENDPOINT_URL#${GIDO_EKS_S3_ENDPOINT_URL}#g" \
  -e "s#CHANGE_ME_RDS_HOST#${GIDO_EKS_RDS_HOST}#g" \
  -e "s#CHANGE_ME_DB_USER#${GIDO_EKS_DB_USER}#g" \
  -e "s#CHANGE_ME_DB_PASSWORD_PLAIN#${GIDO_EKS_DB_PASSWORD}#g" \
  -e "s#CHANGE_ME_DB_NAME#${GIDO_EKS_DB_NAME}#g" \
  -e "s#CHANGE_ME_SECRET_KEY#${GIDO_EKS_SECRET_KEY}#g" \
  -e "s#CHANGE_ME_ARTIFACT_TOKEN#${GIDO_EKS_ARTIFACT_TOKEN}#g" \
  -e "s#CHANGE_ME_ADMIN_PASSWORD#${GIDO_EKS_ADMIN_PASSWORD}#g" \
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
echo "访问: https://${GIDO_EKS_INGRESS_HOST}  或  kubectl -n ${GIDO_EKS_NAMESPACE} port-forward svc/frontend 8080:80"
echo "登录: admin / ${GIDO_EKS_ADMIN_PASSWORD}"
echo ""
echo "部署清单详见: k8s/eks/DEPLOY-GIDO.md"
