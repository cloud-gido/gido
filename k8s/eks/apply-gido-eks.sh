#!/usr/bin/env bash
# 将 gido-eks-external-pg.yaml 中的 CHANGE_ME_* 占位符替换后 apply。
#
# 用法：
#   export GIDO_EKS_ACCOUNT=066158985613
#   export GIDO_EKS_REGION=ap-northeast-1
#   export GIDO_EKS_IMAGE_TAG=v1.0.0
#   export GIDO_EKS_S3_BUCKET=gll-prod-gamelinelab-066158985613
#   export GIDO_EKS_RDS_HOST=mydb.xxx.ap-northeast-1.rds.amazonaws.com
#   export GIDO_EKS_DB_USER=gido
#   export GIDO_EKS_DB_PASSWORD='your-password'
#   export GIDO_EKS_DB_NAME=gido
#   export GIDO_EKS_SECRET_KEY='random-jwt-secret-48chars-minimum'
#   export GIDO_EKS_ARTIFACT_TOKEN='random-artifact-token-32chars'
#   export GIDO_EKS_BACKEND_IRSA=arn:aws:iam::066158985613:role/gido-backend-s3
#   export GIDO_EKS_INGRESS_HOST=gido.example.com
#   bash k8s/eks/apply-gido-eks.sh
#
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="${DIR}/gido-eks-external-pg.yaml"
KUBECTL="${KUBECTL:-kubectl}"

_required() {
  local name="$1" val="${!1:-}"
  if [[ -z "${val}" ]]; then
    echo "错误：请设置环境变量 ${name}" >&2
    exit 1
  fi
}

_required GIDO_EKS_ACCOUNT
_required GIDO_EKS_REGION
_required GIDO_EKS_IMAGE_TAG
_required GIDO_EKS_S3_BUCKET
_required GIDO_EKS_RDS_HOST
_required GIDO_EKS_DB_USER
_required GIDO_EKS_DB_PASSWORD
_required GIDO_EKS_DB_NAME
_required GIDO_EKS_SECRET_KEY
_required GIDO_EKS_ARTIFACT_TOKEN
_required GIDO_EKS_BACKEND_IRSA
_required GIDO_EKS_INGRESS_HOST

# URL 编码密码（python 比 sed 可靠）
DB_PASSWORD_ENC="$(python3 -c "import urllib.parse; print(urllib.parse.quote('''${GIDO_EKS_DB_PASSWORD}''', safe=''))")"

echo "==> apply GIDO EKS stack (external RDS)"
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
  "${MANIFEST}" | ${KUBECTL} apply -f -

echo "==> wait rollout"
${KUBECTL} rollout status deployment/gido-backend -n gido --timeout=300s
${KUBECTL} rollout status deployment/gido-frontend -n gido --timeout=180s

echo ""
echo "完成。首次部署请初始化元库表："
echo "  kubectl -n gido exec deploy/gido-backend -- python init_db.py"
echo ""
echo "访问: https://${GIDO_EKS_INGRESS_HOST}  或  kubectl -n gido port-forward svc/frontend 8080:80"
echo "默认账号 admin / admin123（登录后务必改密；生产勿设 GIDO_BOOTSTRAP_ADMIN_PASSWORD）"
