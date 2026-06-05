#!/usr/bin/env bash
# 若将宿主机 kubeconfig 只读挂载到 /root/.kube/host-kubeconfig（例如 compose override 或 docker run -v），
# 则复制到 /tmp 并按下方规则替换 apiserver 地址，供后端自动解析 Application JM 的 NodePort。
set -euo pipefail
if [[ -r /root/.kube/host-kubeconfig ]]; then
  cp /root/.kube/host-kubeconfig /tmp/kube-for-backend
  PATCH="${GIDO_FLINK_K8S_API_PATCH:-}"
  if [[ "${PATCH}" == "disable" ]] || [[ "${PATCH}" == "off" ]]; then
    :
  else
    sed -E -i 's#https://127\.0\.0\.1:[0-9]+#https://kubernetes.docker.internal:6443#g' /tmp/kube-for-backend || true
    sed -E -i 's#https://localhost:[0-9]+#https://kubernetes.docker.internal:6443#g' /tmp/kube-for-backend || true
    if [[ -n "${PATCH}" ]]; then
      IFS=';' read -ra PAIRS <<< "${PATCH}"
      for p in "${PAIRS[@]}"; do
        [[ -z "${p}" ]] && continue
        from="${p%%|*}"
        to="${p#*|}"
        if [[ -n "${from}" ]] && [[ -n "${to}" ]]; then
          sed -i "s#${from}#${to}#g" /tmp/kube-for-backend || true
        fi
      done
    fi
  fi
  export FLINK_K8S_KUBECONFIG_PATH=/tmp/kube-for-backend
fi
exec "$@"
