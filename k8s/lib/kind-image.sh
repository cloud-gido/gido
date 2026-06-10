# Kind 镜像治理：单架构构建 + ctr 导入（避免 manifest list 导致 CreateContainerError）
# Mac M 芯片默认 linux/arm64，与 OrbStack K3s 一致；Intel Mac 为 linux/amd64。
# 由 apply-gido-stack.sh / kind-load-mirror-images.sh source，勿直接执行。

# 宿主机架构 → 默认 Linux 平台（可用 GIDO_BUILD_PLATFORM / KIND_PLATFORM 覆盖）
gido_host_arch() {
  case "$(uname -m)" in
    arm64|aarch64) echo arm64 ;;
    x86_64|amd64) echo amd64 ;;
    *) echo amd64 ;;
  esac
}

gido_default_linux_platform() {
  case "$(gido_host_arch)" in
    arm64) echo linux/arm64 ;;
    *) echo linux/amd64 ;;
  esac
}

# K8s 节点架构（需 KUBECONFIG 可用）；失败时无输出
gido_k8s_node_arch() {
  local kubectl="${KUBECTL:-kubectl}"
  command -v "${kubectl}" >/dev/null 2>&1 || return 1
  local arch
  arch="$("${kubectl}" get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}' 2>/dev/null || true)"
  case "${arch}" in
    arm64|amd64) echo "${arch}" ;;
    *) return 1 ;;
  esac
}

arch_to_linux_platform() {
  case "$1" in
    arm64|aarch64) echo linux/arm64 ;;
    amd64|x86_64) echo linux/amd64 ;;
    linux/arm64|linux/amd64) echo "$1" ;;
    *) echo linux/amd64 ;;
  esac
}

# 构建目标平台：① GIDO_BUILD_PLATFORM ② 集群节点 ③ 本机 CPU
gido_detect_build_platform() {
  if [[ -n "${GIDO_BUILD_PLATFORM:-}" ]]; then
    arch_to_linux_platform "${GIDO_BUILD_PLATFORM}"
    return 0
  fi
  local arch
  if arch="$(gido_k8s_node_arch 2>/dev/null)"; then
    arch_to_linux_platform "${arch}"
    return 0
  fi
  gido_default_linux_platform
}

gido_detect_build_platform_source() {
  if [[ -n "${GIDO_BUILD_PLATFORM:-}" ]]; then
    echo "GIDO_BUILD_PLATFORM"
    return 0
  fi
  if gido_k8s_node_arch >/dev/null 2>&1; then
    echo "kubectl 集群节点"
    return 0
  fi
  echo "本机 CPU ($(gido_host_arch))"
}

platform_to_arch() {
  case "$1" in
    linux/arm64|arm64) echo arm64 ;;
    linux/amd64|amd64) echo amd64 ;;
    *) echo amd64 ;;
  esac
}

kind_image_log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

kind_image_ensure_buildx() {
  if docker buildx version >/dev/null 2>&1; then
    if ! docker buildx inspect gido-kind-builder >/dev/null 2>&1; then
      docker buildx create --name gido-kind-builder --use >/dev/null 2>&1 || docker buildx use default
    else
      docker buildx use gido-kind-builder >/dev/null 2>&1 || true
    fi
  fi
}

# 构建单架构镜像写入本地 Docker（禁止 OCI index / attestation，Kind containerd 才能稳定解包）
kind_image_build() {
  local platform="${1:-$(gido_default_linux_platform)}"
  local tag="$2"
  local context="$3"
  local expected_arch
  expected_arch="$(platform_to_arch "${platform}")"
  shift 3 || true

  kind_image_log "构建 ${tag} (${platform}, 单架构) …"
  kind_image_ensure_buildx
  docker buildx build \
    --platform "${platform}" \
    --provenance=false \
    --sbom=false \
    --load \
    -t "${tag}" \
    "$@" \
    "${context}"

  local arch
  arch="$(docker image inspect "${tag}" --format '{{.Architecture}}' 2>/dev/null || echo "")"
  if [[ "${arch}" != "${expected_arch}" ]]; then
    echo "错误：${tag} 构建后架构为 ${arch:-未知}，需要 ${expected_arch}（${platform}）" >&2
    exit 1
  fi
  kind_image_verify_single_arch "${tag}"
  kind_image_log "构建完成: ${tag} (${expected_arch})"
}

# Kind containerd 无法解包 OCI manifest index（多架构 attestation/index）
kind_image_is_single_arch() {
  local tag="$1"
  local rootfs_type media
  rootfs_type="$(docker image inspect "${tag}" --format '{{.RootFS.Type}}' 2>/dev/null || echo "")"
  if [[ "${rootfs_type}" == "layers" ]]; then
    return 0
  fi
  media="$(docker image inspect "${tag}" --format '{{json .Descriptor}}' 2>/dev/null || echo "")"
  [[ "${media}" != *"application/vnd.oci.image.index"* ]]
}

kind_image_verify_single_arch() {
  local tag="$1"
  if kind_image_is_single_arch "${tag}"; then
    return 0
  fi
  echo "错误：${tag} 为 OCI image index（多架构清单），Kind 会报 no match for platform。" >&2
  echo "请用 kind_image_build / kind_image_pull_flatten 压平为单架构后再导入。" >&2
  exit 1
}

# 国内 mirror（如 daocloud）对 --platform 常忽略，压平时须用 docker.io 上游才能拿到真 amd64
kind_image_upstream_ref() {
  local ref="$1"
  local mirror="${KIND_MIRROR:-docker.m.daocloud.io}"
  local path="${ref}"
  if [[ "${ref}" == docker.io/* ]]; then
    echo "${ref}"
    return
  fi
  if [[ "${ref}" == "${mirror}/"* ]]; then
    path="${ref#${mirror}/}"
  fi
  echo "docker.io/${path}"
}

# docker pull 在 Mac 上常留下 manifest list / 错误架构；用 buildx 从上游再打一層得到单架构镜像
kind_image_flatten_pull() {
  local tag="$1"
  local source="$2"
  local platform="$3"
  local upstream
  upstream="$(kind_image_upstream_ref "${source}")"
  local tmp_tag="${tag%%:*}:kind-flat-$$"
  local ctx df
  ctx="$(mktemp -d)"
  df="${ctx}/Dockerfile"
  printf 'FROM --platform=%s %s\n' "${platform}" "${upstream}" > "${df}"
  kind_image_log "压平 → 单镜像 ${tag} (${platform})，上游 ${upstream}"
  kind_image_ensure_buildx
  docker buildx build \
    --platform "${platform}" \
    --provenance=false \
    --sbom=false \
    --load \
    -t "${tmp_tag}" \
    -f "${df}" \
    "${ctx}"
  rm -rf "${ctx}"
  docker tag "${tmp_tag}" "${tag}"
  docker rmi "${tmp_tag}" 2>/dev/null || true
}

kind_image_node() {
  local kind_name="${1:-}"
  if [[ -z "${kind_name}" ]]; then
    kind_name="$(kind get clusters 2>/dev/null | head -1 || true)"
  fi
  if [[ -z "${kind_name}" ]]; then
    echo "错误：未找到 Kind 集群" >&2
    return 1
  fi
  echo "${kind_name}-control-plane"
}

# 始终 ctr import；导入前删除节点同名镜像，避免残留 manifest index
kind_image_import() {
  local tag="$1"
  local kind_name="${2:-}"
  local node
  node="$(kind_image_node "${kind_name}")" || return 1

  kind_image_log "导入 Kind 节点 ${node}: ${tag}"
  kind_image_verify_single_arch "${tag}"
  docker exec "${node}" ctr -n=k8s.io images rm "docker.io/library/${tag%%:*}" 2>/dev/null || true
  docker exec "${node}" ctr -n=k8s.io images rm "docker.io/${tag}" 2>/dev/null || true

  docker save "${tag}" | docker exec -i "${node}" ctr -n=k8s.io images import -

  if ! docker exec "${node}" ctr -n=k8s.io images ls -q | grep -qF "${tag%%:*}"; then
    echo "错误：${tag} 导入 Kind 后未在节点镜像列表中找到" >&2
    return 1
  fi
  kind_image_log "导入完成: ${tag}"
}

kind_image_pull_flatten() {
  local tag="$1"
  local pull_ref="${2:-$1}"
  local platform="${3:-$(gido_default_linux_platform)}"
  local expected_arch arch
  expected_arch="$(platform_to_arch "${platform}")"
  arch="$(docker image inspect "${tag}" --format '{{.Architecture}}' 2>/dev/null || echo "")"
  if [[ "${arch}" == "${expected_arch}" ]] && kind_image_is_single_arch "${tag}"; then
    kind_image_log "已有单架构 ${expected_arch}: ${tag}"
    return 0
  fi
  # mirror 的 docker pull 在 Mac 上常得到错误架构或 manifest index，buildx 压平更可靠
  kind_image_flatten_pull "${tag}" "${pull_ref}" "${platform}"
  arch="$(docker image inspect "${tag}" --format '{{.Architecture}}' 2>/dev/null || echo "")"
  if [[ "${arch}" != "${expected_arch}" ]]; then
    echo "错误：${tag} 压平后架构为 ${arch:-未知}，需要 ${expected_arch}（${platform}）" >&2
    exit 1
  fi
  kind_image_verify_single_arch "${tag}"
}

# 兼容旧调用名
kind_image_pull_amd64() {
  kind_image_pull_flatten "$@"
}
