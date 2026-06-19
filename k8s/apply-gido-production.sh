#!/usr/bin/env bash
# 生产 K8s：外置 PostgreSQL + GHCR CI 镜像 → 渲染并 apply gido-production-external-pg.yaml
#
#   cp k8s/gido-production.env.example k8s/gido-production.env
#   # 编辑 PG / 密钥 / 镜像 / S3
#   bash k8s/apply-gido-production.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${ROOT}/k8s/gido-production-external-pg.yaml"
ENV_FILE="${GIDO_PRODUCTION_ENV:-${ROOT}/k8s/gido-production.env}"
KUBECTL="${KUBECTL:-kubectl}"
RENDERED="${GIDO_RENDERED_MANIFEST:-/tmp/gido-production.rendered.yaml}"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
else
  echo "错误：未找到 ${ENV_FILE}，请先 cp k8s/gido-production.env.example k8s/gido-production.env" >&2
  exit 1
fi

_required() {
  local name="$1" val="${!1:-}"
  if [[ -z "${val}" ]]; then
    echo "错误：请设置 ${name}（在 ${ENV_FILE}）" >&2
    exit 1
  fi
}

_required GIDO_PG_HOST
_required GIDO_PG_PORT
_required GIDO_PG_USER
_required GIDO_PG_PASSWORD
_required GIDO_PG_DB
_required GIDO_SECRET_KEY
_required GIDO_ARTIFACT_TOKEN
_required GIDO_ADMIN_PASSWORD

GIDO_GHCR_REPO="${GIDO_GHCR_REPO:-ghcr.io/cloud-gido/gido}"
GIDO_CI_PROFILE="${GIDO_CI_PROFILE:-main}"
GIDO_IMAGE_TAG="${GIDO_IMAGE_TAG:-latest}"
GIDO_FLINK_RUNTIME_VERSION="${GIDO_FLINK_RUNTIME_VERSION:-2.2.1}"
GIDO_FLINK_OPERATOR_FLINK_VERSION="${GIDO_FLINK_OPERATOR_FLINK_VERSION:-v2_2}"
GIDO_STORAGE_CLASS="${GIDO_STORAGE_CLASS:-}"
GIDO_IMAGE_PULL_SECRET="${GIDO_IMAGE_PULL_SECRET:-ghcr-pull}"
GIDO_FLINK_IMAGE_PULL_SECRETS="${GIDO_FLINK_IMAGE_PULL_SECRETS:-${GIDO_IMAGE_PULL_SECRET}}"

GIDO_S3_BUCKET="${GIDO_S3_BUCKET:-}"
GIDO_S3_JAR_PREFIX="${GIDO_S3_JAR_PREFIX:-gido-flink}"
GIDO_S3_REGION="${GIDO_S3_REGION:-}"
GIDO_S3_ENDPOINT_URL="${GIDO_S3_ENDPOINT_URL:-}"
GIDO_S3_AUTH_MODE="${GIDO_S3_AUTH_MODE:-static}"
GIDO_S3_USE_IRSA="${GIDO_S3_USE_IRSA:-false}"

if [[ -n "${GIDO_S3_BUCKET}" ]]; then
  FLINK_OPERATOR_JAR_S3_PREFIX="s3://${GIDO_S3_BUCKET}/${GIDO_S3_JAR_PREFIX}"
  PAIMON_WAREHOUSE_DEFAULT="s3a://${GIDO_S3_BUCKET}/paimon-warehouse"
  FLINK_OPERATOR_CHECKPOINT_DIR="s3a://${GIDO_S3_BUCKET}/flink/checkpoints"
  FLINK_OPERATOR_SAVEPOINT_DIR="s3a://${GIDO_S3_BUCKET}/flink/savepoints"
else
  FLINK_OPERATOR_JAR_S3_PREFIX=""
  PAIMON_WAREHOUSE_DEFAULT="file:///opt/flink/paimon-warehouse"
  FLINK_OPERATOR_CHECKPOINT_DIR=""
  FLINK_OPERATOR_SAVEPOINT_DIR=""
fi

FLINK_TAG="${GIDO_FLINK_RUNTIME_TAG:-}"
if [[ -z "${FLINK_TAG}" ]]; then
  case "${GIDO_CI_PROFILE}" in
    main) FLINK_TAG="${GIDO_FLINK_RUNTIME_VERSION}" ;;
    dev|dev-1) FLINK_TAG="${GIDO_IMAGE_TAG}" ;;
    *) FLINK_TAG="${GIDO_IMAGE_TAG}" ;;
  esac
fi

case "${GIDO_CI_PROFILE}" in
  main)
    BACKEND_IMAGE="${GIDO_GHCR_REPO}/gido-backend:${GIDO_IMAGE_TAG}"
    FRONTEND_IMAGE="${GIDO_GHCR_REPO}/gido-frontend:${GIDO_IMAGE_TAG}"
    FLINK_OPERATOR_IMAGE="${GIDO_GHCR_REPO}/gido-flink-runtime:${FLINK_TAG}"
    ;;
  dev)
    BACKEND_IMAGE="${GIDO_GHCR_REPO}/gido-backend:${GIDO_IMAGE_TAG}"
    FRONTEND_IMAGE="${GIDO_GHCR_REPO}/gido-frontend:${GIDO_IMAGE_TAG}"
    FLINK_OPERATOR_IMAGE="${GIDO_GHCR_REPO}/gido-flink-runtime:${FLINK_TAG}"
    ;;
  dev-1)
    BACKEND_IMAGE="${GIDO_GHCR_REPO}/dev-1/gido-backend:${GIDO_IMAGE_TAG}"
    FRONTEND_IMAGE="${GIDO_GHCR_REPO}/dev-1/gido-frontend:${GIDO_IMAGE_TAG}"
    FLINK_OPERATOR_IMAGE="${GIDO_GHCR_REPO}/dev-1/flink-runtime/${GIDO_FLINK_RUNTIME_VERSION}:${FLINK_TAG}"
    ;;
  *)
    echo "错误：未知 GIDO_CI_PROFILE=${GIDO_CI_PROFILE}（支持 main / dev / dev-1）" >&2
    exit 1
    ;;
esac

echo "==> 渲染生产清单"
echo "    backend:  ${BACKEND_IMAGE}"
echo "    frontend: ${FRONTEND_IMAGE}"
echo "    flink:    ${FLINK_OPERATOR_IMAGE} (${GIDO_FLINK_OPERATOR_FLINK_VERSION})"
echo "    postgres: ${GIDO_PG_HOST}:${GIDO_PG_PORT}/${GIDO_PG_DB}"
if [[ -n "${GIDO_S3_BUCKET}" ]]; then
  echo "    s3 jar:   ${FLINK_OPERATOR_JAR_S3_PREFIX}"
else
  echo "    s3 jar:   (未配置，使用 PVC + HTTP)"
fi

sed \
  -e "s#__BACKEND_IMAGE__#${BACKEND_IMAGE}#g" \
  -e "s#__FRONTEND_IMAGE__#${FRONTEND_IMAGE}#g" \
  -e "s#__FLINK_OPERATOR_IMAGE__#${FLINK_OPERATOR_IMAGE}#g" \
  -e "s#__FLINK_OPERATOR_FLINK_VERSION__#${GIDO_FLINK_OPERATOR_FLINK_VERSION}#g" \
  -e "s#__FLINK_OPERATOR_IMAGE_PULL_SECRETS__#${GIDO_FLINK_IMAGE_PULL_SECRETS}#g" \
  -e "s#__PG_HOST__#${GIDO_PG_HOST}#g" \
  -e "s#__PG_PORT__#${GIDO_PG_PORT}#g" \
  -e "s#__PG_USER__#${GIDO_PG_USER}#g" \
  -e "s#__PG_PASSWORD__#${GIDO_PG_PASSWORD}#g" \
  -e "s#__PG_DB__#${GIDO_PG_DB}#g" \
  -e "s#__SECRET_KEY__#${GIDO_SECRET_KEY}#g" \
  -e "s#__ARTIFACT_TOKEN__#${GIDO_ARTIFACT_TOKEN}#g" \
  -e "s#__ADMIN_PASSWORD__#${GIDO_ADMIN_PASSWORD}#g" \
  -e "s#__GIDO_IMAGE_PULL_SECRET__#${GIDO_IMAGE_PULL_SECRET}#g" \
  -e "s#__STORAGE_CLASS__#${GIDO_STORAGE_CLASS}#g" \
  -e "s#__FLINK_OPERATOR_JAR_S3_PREFIX__#${FLINK_OPERATOR_JAR_S3_PREFIX}#g" \
  -e "s#__GIDO_ARTIFACT_S3_REGION__#${GIDO_S3_REGION}#g" \
  -e "s#__GIDO_ARTIFACT_S3_ENDPOINT_URL__#${GIDO_S3_ENDPOINT_URL}#g" \
  -e "s#__FLINK_OPERATOR_S3_AUTH_MODE__#${GIDO_S3_AUTH_MODE}#g" \
  -e "s#__FLINK_OPERATOR_S3_USE_IRSA__#${GIDO_S3_USE_IRSA}#g" \
  -e "s#__PAIMON_WAREHOUSE_DEFAULT__#${PAIMON_WAREHOUSE_DEFAULT}#g" \
  -e "s#__FLINK_OPERATOR_CHECKPOINT_DIR__#${FLINK_OPERATOR_CHECKPOINT_DIR}#g" \
  -e "s#__FLINK_OPERATOR_SAVEPOINT_DIR__#${FLINK_OPERATOR_SAVEPOINT_DIR}#g" \
  "${MANIFEST}" > "${RENDERED}"

if [[ -z "${GIDO_STORAGE_CLASS}" ]]; then
  sed -i '/storageClassName: ""/d' "${RENDERED}" 2>/dev/null || \
    sed -i '' '/storageClassName: ""/d' "${RENDERED}"
fi

if [[ -z "${GIDO_IMAGE_PULL_SECRET}" || "${GIDO_IMAGE_PULL_SECRET}" == "none" ]]; then
  sed -i '/imagePullSecrets:/,+1d' "${RENDERED}" 2>/dev/null || \
    sed -i '' '/imagePullSecrets:/,+1d' "${RENDERED}"
  sed -i '/FLINK_OPERATOR_IMAGE_PULL_SECRETS:/d' "${RENDERED}" 2>/dev/null || \
    sed -i '' '/FLINK_OPERATOR_IMAGE_PULL_SECRETS:/d' "${RENDERED}"
fi

echo "==> 已写入 ${RENDERED}"
echo "==> kubectl apply"
${KUBECTL} apply -f "${RENDERED}"

echo "==> rollout restart backend（加载 Secret/ConfigMap）"
${KUBECTL} rollout restart deployment/gido-backend -n gido

echo "==> wait rollout"
${KUBECTL} rollout status deployment/gido-backend -n gido --timeout=300s
${KUBECTL} rollout status deployment/gido-frontend -n gido --timeout=180s

echo ""
echo "完成。首次部署请初始化元库："
echo "  kubectl -n gido exec deploy/gido-backend -- python init_db.py"
echo ""
echo "访问: kubectl -n gido port-forward svc/frontend 8080:80"
echo "登录: admin / （GIDO_ADMIN_PASSWORD）"
echo ""
echo "Flink Operator 控制器须已为 1.15：kubectl -n flink-operator get pods"
