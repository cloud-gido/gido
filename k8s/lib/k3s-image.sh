# 局域网 K3s / OrbStack：构建单架构镜像并推送到集群内 registry
# 平台由 kind-image.sh gido_detect_build_platform 自动检测（集群节点 > 本机 CPU）。

k3s_image_log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

k3s_image_build() {
  local platform="${1:-linux/arm64}"
  local tag="$2"
  local context="$3"
  shift 3 || true

  k3s_image_log "构建 ${tag} (${platform}) …"
  if docker buildx version >/dev/null 2>&1; then
    if ! docker buildx inspect gido-k3s-builder >/dev/null 2>&1; then
      docker buildx create --name gido-k3s-builder --use >/dev/null 2>&1 || docker buildx use default
    else
      docker buildx use gido-k3s-builder >/dev/null 2>&1 || true
    fi
    docker buildx build \
      --platform "${platform}" \
      --provenance=false \
      --sbom=false \
      --load \
      -t "${tag}" \
      "$@" \
      "${context}"
  else
    docker build -t "${tag}" "$@" "${context}"
  fi
  k3s_image_log "构建完成: ${tag}"
}

# 推送至集群内 registry（须已 apply k8s/registry.yaml 且 port-forward 5001:5000）
k3s_image_push_incluster() {
  local local_tag="$1"
  local registry_host="${2:-localhost:5001}"
  local remote_tag="${registry_host}/${local_tag##*/}"
  if [[ "${local_tag}" == *"/"* ]]; then
    remote_tag="${registry_host}/${local_tag#*/}"
  fi
  k3s_image_log "推送 ${local_tag} -> ${remote_tag}"
  docker tag "${local_tag}" "${remote_tag}"
  docker push "${remote_tag}"
  echo "${remote_tag}"
}

k3s_incluster_image_ref() {
  local name="$1"
  local tag="${2:-latest}"
  echo "registry.gido.svc.cluster.local:5000/${name}:${tag}"
}

# 本机 Docker 镜像 → K3s containerd（与 Kind 的 kind load 同类，不走 registry / HTTPS）
k3s_ctr_import_cmd() {
  if command -v k3s >/dev/null 2>&1 && k3s ctr version >/dev/null 2>&1; then
    echo "k3s ctr -n k8s.io images import -"
    return 0
  fi
  if sudo -n k3s ctr version >/dev/null 2>&1; then
    echo "sudo k3s ctr -n k8s.io images import -"
    return 0
  fi
  return 1
}

k3s_image_import_local() {
  local tag="$1"
  local import_cmd
  if ! import_cmd="$(k3s_ctr_import_cmd)"; then
    return 1
  fi
  k3s_image_log "本机导入 containerd: ${tag}"
  docker save "${tag}" | eval "${import_cmd}"
}

k3s_image_import_ssh() {
  local tag="$1"
  local host="${2:?K3S SSH host}"
  local user="${3:-${USER}}"
  k3s_image_log "SSH 导入 ${user}@${host}: ${tag}"
  docker save "${tag}" | ssh "${user}@${host}" 'sudo k3s ctr -n k8s.io images import -'
}

k3s_image_import_kubectl() {
  local tag="$1"
  local node="${2:?node name}"
  local kubectl="${3:-kubectl}"
  k3s_image_log "kubectl debug 导入节点 ${node}: ${tag}"
  docker save "${tag}" | ${kubectl} debug "node/${node}" -q \
    --image=docker.m.daocloud.io/library/alpine:3.20 \
    --profile=general \
    -- chroot /host ctr -n k8s.io images import -
}

# 按环境自动选择：本机 k3s → SSH → kubectl debug
k3s_image_import_to_cluster() {
  local tag="$1"
  local node="${K3S_NODE_NAME:-}"
  local kubectl="${KUBECTL:-kubectl}"
  if k3s_ctr_import_cmd >/dev/null 2>&1; then
    k3s_image_import_local "${tag}"
    return 0
  fi
  if [[ -n "${K3S_SSH_HOST:-}" ]]; then
    k3s_image_import_ssh "${tag}" "${K3S_SSH_HOST}" "${K3S_SSH_USER:-${USER}}"
    return 0
  fi
  if [[ -z "${node}" ]]; then
    node="$(${kubectl} get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  fi
  if [[ -n "${node}" ]]; then
    k3s_image_import_kubectl "${tag}" "${node}" "${kubectl}"
    return 0
  fi
  echo "错误：无法导入 ${tag}。请在 K3s 节点本机执行、或设置 K3S_SSH_HOST、或确认 kubectl 可 debug node。" >&2
  return 1
}
