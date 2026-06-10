# 局域网 K3s：集群内 HTTP registry + 本机 docker push + 节点 pull（准生产路径）
# 由 apply-gido-k3s-registry.sh / apply-gido-orbstack.sh（GIDO_K3S_USE_REGISTRY=1）source

K3S_REGISTRY_HOST="${K3S_REGISTRY_HOST:-registry.gido.svc.cluster.local:5000}"

k3s_registry_log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

k3s_registry_primary_node() {
  local kubectl="${KUBECTL:-kubectl}"
  ${kubectl} get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

# SSH 仅在使用方显式设置 K3S_SSH_HOST 时启用（勿用 API 地址 192.168.1.68，OrbStack 节点在 ubuntu VM）
k3s_registry_detect_ssh_host() {
  if [[ -n "${K3S_SSH_HOST:-}" ]]; then
    echo "${K3S_SSH_HOST}"
    return 0
  fi
  return 1
}

k3s_registry_image_ref() {
  local name="$1"
  local tag="${2:-latest}"
  echo "${K3S_REGISTRY_HOST}/${name}:${tag}"
}

# SSH 不可用时，经 kubectl debug 在节点上执行（无需 22 端口）
k3s_registry_node_exec() {
  local cmd="$1"
  local node kubectl
  kubectl="${KUBECTL:-kubectl}"
  node="${K3S_NODE_NAME:-$(k3s_registry_primary_node)}"
  if [[ -z "${node}" ]]; then
    return 1
  fi
  ${kubectl} debug "node/${node}" -q \
    --image=docker.m.daocloud.io/library/alpine:3.20 \
    --profile=general \
    -- chroot /host /bin/sh -c "${cmd}" >&2
}

k3s_registry_apply_insecure_config() {
  local root="$1"
  local kubectl="${KUBECTL:-kubectl}"
  k3s_registry_log "apply k3s-insecure-registry.yaml（mirrors + HTTP endpoint）"
  k3s_registry_kubectl_apply -f "${root}/k8s/k3s-insecure-registry.yaml" >&2
}

k3s_registry_restart_k3s() {
  if [[ "${GIDO_K3S_SKIP_RESTART:-}" == "1" ]]; then
    k3s_registry_log "跳过 K3s 重启（GIDO_K3S_SKIP_RESTART=1）；节点 pull 可能仍走 HTTPS"
    return 0
  fi

  local host user
  host="$(k3s_registry_detect_ssh_host)" || host=""
  user="${K3S_SSH_USER:-${USER}}"

  if [[ -n "${host}" ]]; then
    k3s_registry_log "SSH 重启 K3s: ${user}@${host}"
    if ssh -o BatchMode=yes -o ConnectTimeout=10 "${user}@${host}" 'sudo systemctl restart k3s' >&2; then
      return 0
    fi
    k3s_registry_log "SSH 不可用（Connection refused 等），改用 kubectl debug 重启 k3s"
  fi

  local node
  node="${K3S_NODE_NAME:-$(k3s_registry_primary_node)}"
  if [[ -z "${node}" ]]; then
    k3s_registry_log "错误：无法确定节点名，请设置 K3S_NODE_NAME 或在节点执行: sudo systemctl restart k3s"
    return 1
  fi
  k3s_registry_log "kubectl debug 重启 K3s（节点 ${node}）"
  if k3s_registry_node_exec 'sudo systemctl restart k3s'; then
    return 0
  fi
  k3s_registry_log "kubectl debug 重启失败。请在节点执行: sudo systemctl restart k3s"
  return 1
}

k3s_registry_wait_api_ready() {
  local kubectl="${KUBECTL:-kubectl}"
  local max="${K3S_API_WAIT_SEC:-120}"
  local i=0
  k3s_registry_log "等待 K8s API 就绪（restart k3s 后 openapi 可能短暂不可用）…"
  while [[ "${i}" -lt "${max}" ]]; do
    if ${kubectl} get --raw='/readyz?verbose=0' >/dev/null 2>&1; then
      k3s_registry_log "API 已就绪"
      return 0
    fi
    sleep 2
    i=$((i + 2))
  done
  k3s_registry_log "警告：API 在 ${max}s 内未就绪，后续 apply 将重试"
  return 1
}

k3s_registry_wait_nodes_ready() {
  local kubectl="${KUBECTL:-kubectl}"
  k3s_registry_wait_api_ready || true
  k3s_registry_log "等待节点 Ready …"
  local i=0
  while [[ "${i}" -lt 180 ]]; do
    if ${kubectl} wait --for=condition=Ready node --all --timeout=30s >/dev/null 2>&1; then
      sleep 3
      return 0
    fi
    sleep 3
    i=$((i + 3))
  done
  return 1
}

k3s_registry_kubectl_apply() {
  local kubectl="${KUBECTL:-kubectl}"
  local max=15
  local i=0
  while [[ "${i}" -lt "${max}" ]]; do
    if ${kubectl} apply "$@"; then
      return 0
    fi
    k3s_registry_log "kubectl apply 失败，${i}/$((max - 1)) 次重试（API 可能仍在恢复）…"
    sleep 5
    i=$((i + 1))
  done
  return 1
}

k3s_registry_verify_node_pull() {
  local image="$1"
  if [[ "${GIDO_K3S_SKIP_PULL_TEST:-}" == "1" ]]; then
    return 0
  fi

  k3s_registry_log "节点试拉镜像: ${image}"

  local host user
  host="$(k3s_registry_detect_ssh_host)" || host=""
  user="${K3S_SSH_USER:-${USER}}"
  if [[ -n "${host}" ]]; then
    if ssh -o BatchMode=yes -o ConnectTimeout=10 "${user}@${host}" \
      "sudo k3s crictl pull '${image}'" 2>/dev/null; then
      k3s_registry_log "试拉成功（SSH）: ${image}"
      return 0
    fi
  fi

  if k3s_registry_node_exec "sudo k3s crictl pull '${image}'"; then
    k3s_registry_log "试拉成功（kubectl debug）: ${image}"
    return 0
  fi

  k3s_registry_log "警告：节点试拉失败。若未 restart k3s，请重试或检查 mirrors 配置。"
  return 1
}

k3s_registry_ensure_deployment() {
  local root="$1"
  local kubectl="${KUBECTL:-kubectl}"
  k3s_registry_kubectl_apply -f "${root}/k8s/registry.yaml" >&2
  ${kubectl} rollout status deployment/registry -n gido --timeout=120s >&2
}

k3s_registry_push_images() {
  local pf_port="${1:-5001}"
  shift || true
  local kubectl="${KUBECTL:-kubectl}"
  local img

  K3S_REGISTRY_PF_PID=""
  k3s_registry_pf_cleanup() {
    if [[ -n "${K3S_REGISTRY_PF_PID:-}" ]] && kill -0 "${K3S_REGISTRY_PF_PID}" 2>/dev/null; then
      kill "${K3S_REGISTRY_PF_PID}" 2>/dev/null || true
    fi
    K3S_REGISTRY_PF_PID=""
    trap - RETURN
  }
  trap k3s_registry_pf_cleanup RETURN

  k3s_registry_log "port-forward registry ${pf_port}:5000"
  ${kubectl} port-forward -n gido svc/registry "${pf_port}:5000" >/dev/null 2>&1 &
  K3S_REGISTRY_PF_PID=$!
  sleep 2

  for img in "$@"; do
    [[ -n "${img}" ]] || continue
    k3s_image_push_incluster "${img}" "localhost:${pf_port}" >/dev/null
  done

  if command -v curl >/dev/null 2>&1; then
    curl -sf "http://127.0.0.1:${pf_port}/v2/_catalog" | grep -q gido-backend || {
      k3s_registry_log "错误：registry 中未见 gido-backend"
      return 1
    }
  fi
}

k3s_registry_rollout_gido() {
  local kubectl="${KUBECTL:-kubectl}"
  k3s_registry_log "rollout restart gido-backend / gido-frontend"
  ${kubectl} -n gido rollout restart deployment/gido-backend deployment/gido-frontend >&2
  ${kubectl} -n gido rollout status deployment/gido-backend --timeout=300s >&2
  ${kubectl} -n gido rollout status deployment/gido-frontend --timeout=180s >&2
}

# 配置 → 重启 k3s → registry → push → 试拉（镜像引用由调用方用 k3s_registry_image_ref 计算）
k3s_registry_publish_images() {
  local root="$1"
  local local_backend="$2"
  local local_frontend="$3"
  local image_tag="$4"
  local local_flink="${5:-}"

  k3s_registry_apply_insecure_config "${root}"
  if ! k3s_registry_restart_k3s; then
    k3s_registry_log "继续 push（但节点 pull 可能失败，直至 k3s 重启成功）"
  fi
  k3s_registry_wait_nodes_ready || true

  k3s_registry_ensure_deployment "${root}"
  local pf_port="${GIDO_REGISTRY_PF_PORT:-5001}"
  if [[ -n "${local_flink}" ]]; then
    k3s_registry_push_images "${pf_port}" "${local_backend}" "${local_frontend}" "${local_flink}"
  else
    k3s_registry_push_images "${pf_port}" "${local_backend}" "${local_frontend}"
  fi

  local backend_ref flink_ref
  backend_ref="$(k3s_registry_image_ref "gido-backend" "${image_tag}")"
  if ! k3s_registry_verify_node_pull "${backend_ref}"; then
    if [[ "${GIDO_K3S_SKIP_RESTART:-}" == "1" ]]; then
      k3s_registry_log "试拉失败属预期：已跳过 restart。请 restart k3s 后重新 rollout。"
    fi
  fi
  if [[ -n "${local_flink}" ]]; then
    flink_ref="$(k3s_registry_image_ref "gido-flink-sql-runner" "${image_tag}")"
    k3s_registry_verify_node_pull "${flink_ref}" || true
  fi
}
