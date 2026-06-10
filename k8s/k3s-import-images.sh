#!/usr/bin/env bash
# 将本地 GIDO 镜像直接导入 K3s 节点 containerd（绕过 HTTP registry 拉取问题）
#
# 适用：ImagePullBackOff 且 Head "https://registry.../v2/...": EOF
# 前置：apply-gido-orbstack.sh 已构建 gido-backend:orbstack / gido-frontend:orbstack
#
# 用法：
#   export KUBECONFIG=~/.kube/config-mac-orbstack
#   bash k8s/k3s-import-images.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
NODE="${K3S_NODE_NAME:-ubuntu}"
TAG="${GIDO_IMAGE_TAG:-orbstack}"
BACKEND_LOCAL="gido-backend:${TAG}"
FRONTEND_LOCAL="gido-frontend:${TAG}"
BACKEND_REMOTE="registry.gido.svc.cluster.local:5000/gido-backend:${TAG}"
FRONTEND_REMOTE="registry.gido.svc.cluster.local:5000/gido-frontend:${TAG}"

for img in "${BACKEND_LOCAL}" "${FRONTEND_LOCAL}"; do
  if ! docker image inspect "${img}" >/dev/null 2>&1; then
    echo "错误：本地无镜像 ${img}，请先运行 bash k8s/apply-gido-orbstack.sh" >&2
    exit 1
  fi
done

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

import_one() {
  local local_tag="$1"
  local remote_tag="$2"
  local tar="${tmpdir}/$(echo "${remote_tag}" | tr '/:' '_').tar"
  echo "==> 打包 ${local_tag} -> ${remote_tag}"
  docker tag "${local_tag}" "${remote_tag}"
  docker save "${remote_tag}" -o "${tar}"
  echo "==> 导入节点 ${NODE}: ${remote_tag}"
  ${KUBECTL} debug "node/${NODE}" -q \
    --image=docker.m.daocloud.io/library/alpine:3.20 \
    --profile=general \
    --copy-to="gido-import-${RANDOM}" \
    -- sleep 1 >/dev/null 2>&1 || true
  # 通过 hostPath 调试 Pod 将 tar 流式写入节点 containerd
  cat "${tar}" | ${KUBECTL} debug "node/${NODE}" -q \
    --image=docker.m.daocloud.io/library/alpine:3.20 \
    --profile=general \
    -- chroot /host ctr -n k8s.io images import - 2>&1
  ${KUBECTL} debug "node/${NODE}" -q \
    --image=docker.m.daocloud.io/library/alpine:3.20 \
    --profile=general \
    -- chroot /host ctr -n k8s.io images ls -q 2>&1 | grep -F "${remote_tag%%:*}" || {
      echo "警告：未在节点镜像列表中确认 ${remote_tag}，若仍 ImagePullBackOff 请在节点执行: sudo systemctl restart k3s" >&2
    }
}

import_one "${BACKEND_LOCAL}" "${BACKEND_REMOTE}"
import_one "${FRONTEND_LOCAL}" "${FRONTEND_REMOTE}"

echo ""
echo "==> 重启 GIDO Deployment"
${KUBECTL} -n gido rollout restart deployment/gido-backend deployment/gido-frontend
${KUBECTL} -n gido rollout status deployment/gido-backend --timeout=300s
${KUBECTL} -n gido rollout status deployment/gido-frontend --timeout=180s
echo "完成。"
