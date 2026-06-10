#!/usr/bin/env bash

# 将 Flink Kubernetes Operator 升级到 1.15.0（与 Flink 2.0.1 技术栈对齐）
#
# 用法（须已安装 helm）：
#   KUBECONFIG=~/.kube/kind-gido.yaml bash k8s/upgrade-flink-operator-1.15.sh
#
# 升级前请备份：
#   kubectl get flinkdeployment -A -o yaml > /tmp/flinkdeployment-backup.yaml
#
# 说明：
# - Chart 仓库：https://downloads.apache.org/flink/flink-kubernetes-operator-1.15.0/
# - 直链包名：flink-kubernetes-operator-1.15.0-helm.tgz（不是 flink-kubernetes-operator-1.15.0.tgz）
# - Kind 无 cert-manager：webhook.create=false
# - 作业 SA 仍用 k8s/flink-operator-rbac.yaml：jobServiceAccount.create=false
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPERATOR_VERSION="${FLINK_OPERATOR_CHART_VERSION:-1.15.0}"
OPERATOR_NS="${FLINK_OPERATOR_NAMESPACE:-flink-operator}"
RELEASE_NAME="${FLINK_OPERATOR_RELEASE_NAME:-flink-kubernetes-operator}"
REPO_NAME="${FLINK_OPERATOR_HELM_REPO:-flink-operator-repo}"
REPO_URL="https://downloads.apache.org/flink/flink-kubernetes-operator-${OPERATOR_VERSION}/"
CHART_TGZ_URL="${REPO_URL}flink-kubernetes-operator-${OPERATOR_VERSION}-helm.tgz"

HELM_SET=(
  --set "image.repository=ghcr.io/apache/flink-kubernetes-operator"
  --set "image.tag=${OPERATOR_VERSION}"
  --set "webhook.create=false"
  --set "jobServiceAccount.create=false"
  --set "watchNamespaces={flink}"
)

echo "==> 目标：Operator ${OPERATOR_VERSION}，namespace=${OPERATOR_NS}，release=${RELEASE_NAME}"
kubectl get nodes
kubectl get crd flinkdeployments.flink.apache.org >/dev/null 2>&1 || {
  echo "错误：未找到 FlinkDeployment CRD，请先安装 Operator" >&2
  exit 1
}

if ! command -v helm >/dev/null 2>&1; then
  echo "错误：未找到 helm" >&2
  exit 1
fi

echo "==> helm repo add ${REPO_NAME} ${REPO_URL}"
helm repo add "${REPO_NAME}" "${REPO_URL}" 2>/dev/null || helm repo add "${REPO_NAME}" "${REPO_URL}" --force-update
helm repo update "${REPO_NAME}"

# Helm 不会自动升级已存在的 CRD；1.15 新增 flinkbluegreendeployments 等
echo "==> 应用 Operator ${OPERATOR_VERSION} CRD（从 *-helm.tgz 解包）"
CRD_TMP="$(mktemp -d)"
trap 'rm -rf "${CRD_TMP}"' EXIT
curl -fsSL "${CHART_TGZ_URL}" -o "${CRD_TMP}/operator-helm.tgz"
tar -xzf "${CRD_TMP}/operator-helm.tgz" -C "${CRD_TMP}"
kubectl apply -f "${CRD_TMP}/flink-kubernetes-operator/crds/"

kubectl create namespace "${OPERATOR_NS}" --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace flink --dry-run=client -o yaml | kubectl apply -f -

# k8s/flink-operator-rbac.yaml 手工创建的 Role/RoleBinding 会阻塞 Helm 接管 namespace RBAC
echo "==> 临时移除 flink ns 中非 Helm 管理的 Role/RoleBinding（升级后由脚本重新 apply RBAC）"
kubectl delete role,rolebinding flink -n flink --ignore-not-found

CHART_REF="${REPO_NAME}/flink-kubernetes-operator"
if helm status "${RELEASE_NAME}" -n "${OPERATOR_NS}" >/dev/null 2>&1; then
  echo "==> helm upgrade ${RELEASE_NAME} (${OPERATOR_VERSION})"
  helm upgrade "${RELEASE_NAME}" "${CHART_REF}" \
    --namespace "${OPERATOR_NS}" \
    --version "${OPERATOR_VERSION}" \
    "${HELM_SET[@]}" || {
      echo "upgrade 失败，尝试 helm upgrade --install …" >&2
      helm upgrade --install "${RELEASE_NAME}" "${CHART_REF}" \
        --namespace "${OPERATOR_NS}" \
        --version "${OPERATOR_VERSION}" \
        "${HELM_SET[@]}"
    }
else
  echo "==> helm install ${RELEASE_NAME} (${OPERATOR_VERSION})"
  if ! helm install "${RELEASE_NAME}" "${CHART_REF}" \
    --namespace "${OPERATOR_NS}" \
    --version "${OPERATOR_VERSION}" \
    "${HELM_SET[@]}"; then
    echo "repo install 失败，尝试 *-helm.tgz …" >&2
    TMP="$(mktemp -d)"
    trap 'rm -rf "${TMP}"' EXIT
    curl -fsSL "${CHART_TGZ_URL}" -o "${TMP}/operator-helm.tgz"
    helm install "${RELEASE_NAME}" "${TMP}/operator-helm.tgz" \
      --namespace "${OPERATOR_NS}" \
      "${HELM_SET[@]}"
  fi
fi

echo "==> 等待 Operator Pod"
kubectl rollout status deployment -n "${OPERATOR_NS}" --timeout=300s

echo "==> 确保作业 Pod RBAC（k8s/flink-operator-rbac.yaml）"
kubectl apply -f "${ROOT}/k8s/flink-operator-rbac.yaml"

echo ""
echo "完成。请确认："
echo "  helm list -n ${OPERATOR_NS}"
echo "  kubectl get pods -n ${OPERATOR_NS}"
echo "  kubectl get deploy -n ${OPERATOR_NS} -o jsonpath='{.items[0].spec.template.spec.containers[0].image}{\"\\n\"}'"
