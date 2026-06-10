

# GIDO frontend port-forward 后台启动（在持有 kubeconfig 的机器上执行，通常是 Mac）
# 由 apply-gido-stack.sh / apply-gido-k3s-registry.sh source，或 k8s/gido-port-forward.sh 单独调用

gido_pf_log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

gido_pf_detect_lan_ip() {
  ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true
}

gido_pf_match_pids() {
  local port="${1:-${GIDO_PF_PORT:-8080}}"
  # 实际命令: kubectl -n gido port-forward ... svc/frontend ${port}:80
  pgrep -f "kubectl.*-n[[:space:]]+gido[[:space:]]+port-forward.*frontend.*${port}:80" 2>/dev/null || true
}

gido_pf_stop() {
  local port="${GIDO_PF_PORT:-8080}"
  local pids
  pids="$(gido_pf_match_pids "${port}")"
  if [[ -z "${pids}" ]]; then
    local pid
    for pid in $(lsof -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true); do
      if ps -p "${pid}" -o args= 2>/dev/null | grep -q "kubectl.*port-forward"; then
        pids="${pids} ${pid}"
      fi
    done
    pids="${pids# }"
  fi
  if [[ -n "${pids}" ]]; then
    gido_pf_log "停止已有 port-forward (pid: ${pids})"
    kill ${pids} 2>/dev/null || true
    sleep 1
  fi
}

gido_pf_start() {
  local kubectl="${KUBECTL:-kubectl}"
  local port="${GIDO_PF_PORT:-8080}"
  local bind="${GIDO_PF_BIND:-0.0.0.0}"
  local log="${GIDO_PF_LOG:-/tmp/gido-pf.log}"

  if ! ${kubectl} -n gido get svc frontend >/dev/null 2>&1; then
    gido_pf_log "跳过 port-forward：gido/frontend Service 不存在"
    return 1
  fi

  gido_pf_stop

  gido_pf_log "后台启动 port-forward ${bind}:${port} → gido/frontend:80（日志 ${log}）"
  nohup ${kubectl} -n gido port-forward --address "${bind}" "svc/frontend" "${port}:80" \
    >>"${log}" 2>&1 &
  local pid=$!
  sleep 2
  if ! kill -0 "${pid}" 2>/dev/null; then
    gido_pf_log "port-forward 启动失败，查看 ${log}"
    tail -5 "${log}" >&2 || true
    return 1
  fi

  local lan
  lan="$(gido_pf_detect_lan_ip)"
  gido_pf_log "port-forward 已启动 pid=${pid}"
  echo "  本机:     http://127.0.0.1:${port}"
  if [[ -n "${lan}" ]]; then
    echo "  局域网:   http://${lan}:${port}"
  fi
  echo "  日志:     ${log}"
  echo "  停止:     kill ${pid}  或  bash k8s/gido-port-forward.sh stop"
  return 0
}

gido_pf_maybe_start() {
  if [[ "${GIDO_AUTO_PORT_FORWARD:-1}" == "0" ]]; then
    gido_pf_log "未自动 port-forward（GIDO_AUTO_PORT_FORWARD=0）"
    echo "  手动: kubectl -n gido port-forward --address 0.0.0.0 svc/frontend 8080:80"
    return 0
  fi
  echo ""
  echo "==> 自动后台 port-forward"
  gido_pf_start
}
