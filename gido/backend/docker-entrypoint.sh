#!/usr/bin/env bash
# 若将宿主机 kubeconfig 只读挂载到 /root/.kube/host-kubeconfig（compose 默认），
# 则复制到 /tmp 并按下方规则替换 apiserver 地址，供 Operator / NodePort 解析等在容器内访问 K8s API。
set -euo pipefail

_kind_control_plane_host() {
  local ctx="${FLINK_K8S_CONTEXT:-${GIDO_FLINK_K8S_CONTEXT:-}}"
  local candidates=()
  if [[ -n "${GIDO_FLINK_K8S_KIND_CONTROL_PLANE:-}" ]]; then
    candidates+=("${GIDO_FLINK_K8S_KIND_CONTROL_PLANE}")
  fi
  if [[ -n "${ctx}" ]]; then
    candidates+=("${ctx}-control-plane")
    if [[ "${ctx}" == kind-* ]]; then
      candidates+=("${ctx#kind-}-control-plane")
    fi
  fi
  candidates+=("kind-control-plane")
  local h
  for h in "${candidates[@]}"; do
    [[ -z "${h}" ]] && continue
    if getent hosts "${h}" >/dev/null 2>&1; then
      echo "${h}"
      return 0
    fi
  done
  return 1
}

if [[ -r /root/.kube/host-kubeconfig ]]; then
  cp /root/.kube/host-kubeconfig /tmp/kube-for-backend
  PATCH="${GIDO_FLINK_K8S_API_PATCH:-}"
  API_HOST="${GIDO_FLINK_K8S_API_HOST:-host.docker.internal}"
  if [[ "${PATCH}" == "disable" ]] || [[ "${PATCH}" == "off" ]]; then
    :
  elif [[ "${PATCH}" == "docker-desktop" ]]; then
    sed -E -i 's#https://127\.0\.0\.1:[0-9]+#https://kubernetes.docker.internal:6443#g' /tmp/kube-for-backend || true
    sed -E -i 's#https://localhost:[0-9]+#https://kubernetes.docker.internal:6443#g' /tmp/kube-for-backend || true
  elif [[ -n "${PATCH}" ]] && [[ "${PATCH}" != "kind" ]]; then
    IFS=';' read -ra PAIRS <<< "${PATCH}"
    for p in "${PAIRS[@]}"; do
      [[ -z "${p}" ]] && continue
      from="${p%%|*}"
      to="${p#*|}"
      if [[ -n "${from}" ]] && [[ -n "${to}" ]]; then
        sed -i "s#${from}#${to}#g" /tmp/kube-for-backend || true
      fi
    done
  else
    kind_cp=""
    if kind_cp="$(_kind_control_plane_host)"; then
      # 使用 control-plane 主机名（kind 网 DNS → 192.168.x.x），保留原 CA，避免与 insecure 冲突
      sed -E -i "s#https://127\\.0\\.0\\.1:[0-9]+#https://${kind_cp}:6443#g" /tmp/kube-for-backend || true
      sed -E -i "s#https://localhost:([0-9]+)#https://${kind_cp}:6443#g" /tmp/kube-for-backend || true
    else
      sed -E -i "s#https://127\\.0\\.0\\.1:([0-9]+)#https://${API_HOST}:\\1#g" /tmp/kube-for-backend || true
      sed -E -i "s#https://localhost:([0-9]+)#https://${API_HOST}:\\1#g" /tmp/kube-for-backend || true
      if grep -qE 'host\.docker\.internal|kubernetes\.docker\.internal' /tmp/kube-for-backend \
        && ! grep -q 'insecure-skip-tls-verify' /tmp/kube-for-backend; then
        sed -i '/certificate-authority-data/a\    insecure-skip-tls-verify: true' /tmp/kube-for-backend || true
      fi
    fi
  fi
  export FLINK_K8S_KUBECONFIG_PATH=/tmp/kube-for-backend
fi
exec "$@"
