#!/usr/bin/env bash

# 单独管理 GIDO frontend port-forward（在 Mac / 有 kubeconfig 的机器上执行）
#
#   export KUBECONFIG=~/.kube/config-mac-orbstack
#   bash k8s/gido-port-forward.sh          # 启动（默认 0.0.0.0:8080）
#   bash k8s/gido-port-forward.sh stop
#   bash k8s/gido-port-forward.sh status
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config-mac-orbstack}"
export KUBECTL="${KUBECTL:-kubectl}"

# shellcheck source=lib/gido-port-forward.sh
source "${ROOT}/k8s/lib/gido-port-forward.sh"

cmd="${1:-start}"
case "${cmd}" in
  start)
    gido_pf_start
    ;;
  stop)
    gido_pf_stop
    echo "已停止 gido frontend port-forward"
    ;;
  status)
    port="${GIDO_PF_PORT:-8080}"
    if pids="$(gido_pf_match_pids "${port}")" && [[ -n "${pids}" ]] && ps -p ${pids} -o pid=,args= 2>/dev/null; then
      lan="$(gido_pf_detect_lan_ip)"
      echo "本机: http://127.0.0.1:${port}"
      [[ -n "${lan}" ]] && echo "局域网: http://${lan}:${port}"
    else
      echo "未运行"
      exit 1
    fi
    ;;
  *)
    echo "用法: $0 {start|stop|status}" >&2
    exit 1
    ;;
esac
