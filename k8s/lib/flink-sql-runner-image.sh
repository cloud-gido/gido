

# GIDO 统一 Flink 运行时镜像（sql-runner + Paimon + CDC，Operator 唯一提交路径）
# 镜像名：gido-flink-sql-runner（主）/ gido-flink-runtime（别名）
# 须先 source k8s/lib/kind-image.sh（平台由 gido_detect_build_platform 自动判断）
# 由 apply-gido-stack.sh / apply-gido-k3s-registry.sh / deploy-gido-k3s.sh source

gido_flink_sql_runner_default_tag() {
  echo "gido-flink-sql-runner:${GIDO_IMAGE_TAG:-latest}"
}

gido_flink_runtime_alias_tag() {
  local base_tag="${1:-$(gido_flink_sql_runner_default_tag)}"
  local image_tag="${base_tag#*:}"
  echo "gido-flink-runtime:${image_tag}"
}

gido_flink_sql_runner_build() {
  local platform="${1:-$(gido_detect_build_platform)}"
  local tag="${2:-$(gido_flink_sql_runner_default_tag)}"
  local root="${3:?root dir}"
  local context="${root}/k8s/flink-sql-runner"
  local flink_base="${FLINK_BASE_IMAGE:-docker.m.daocloud.io/apache/flink:2.0.1-java11}"
  local maven_image="${MAVEN_IMAGE:-docker.m.daocloud.io/library/maven:3.9-eclipse-temurin-11}"

  if [[ ! -f "${context}/Dockerfile" ]]; then
    echo "错误：未找到 ${context}/Dockerfile" >&2
    return 1
  fi

  local expected_arch
  expected_arch="$(platform_to_arch "${platform}")"
  printf '[%s] 构建 Flink 运行时 %s | 平台 %s（来源: %s）…\n' \
    "$(date '+%H:%M:%S')" "${tag}" "${platform}" "$(gido_detect_build_platform_source)" >&2
  # 避免本地缓存 amd64 基础镜像导致 InvalidBaseImagePlatform（M 芯片常见）
  docker pull --platform "${platform}" "${flink_base}" >/dev/null 2>&1 || true
  docker pull --platform "${platform}" "${maven_image}" >/dev/null 2>&1 || true
  if docker buildx version >/dev/null 2>&1; then
    if ! docker buildx inspect gido-flink-builder >/dev/null 2>&1; then
      docker buildx create --name gido-flink-builder --use >/dev/null 2>&1 || docker buildx use default
    else
      docker buildx use gido-flink-builder >/dev/null 2>&1 || true
    fi
    docker buildx build \
      --platform "${platform}" \
      --provenance=false \
      --sbom=false \
      --load \
      --build-arg "TARGETPLATFORM=${platform}" \
      --build-arg "FLINK_BASE_IMAGE=${flink_base}" \
      --build-arg "MAVEN_IMAGE=${maven_image}" \
      -t "${tag}" \
      "${context}"
  else
    docker build \
      --build-arg "FLINK_BASE_IMAGE=${flink_base}" \
      --build-arg "MAVEN_IMAGE=${maven_image}" \
      -t "${tag}" \
      "${context}"
  fi
  local alias_tag arch
  alias_tag="$(gido_flink_runtime_alias_tag "${tag}")"
  docker tag "${tag}" "${alias_tag}" 2>/dev/null || true
  arch="$(docker image inspect "${tag}" --format '{{.Architecture}}' 2>/dev/null || echo "")"
  if [[ -n "${expected_arch}" && -n "${arch}" && "${arch}" != "${expected_arch}" ]]; then
    echo "错误：${tag} 架构为 ${arch}，与目标 ${expected_arch}（${platform}）不一致；请清理旧镜像后重试" >&2
    return 1
  fi
  printf '[%s] Flink 运行时构建完成: %s（别名 %s，架构 %s）\n' \
    "$(date '+%H:%M:%S')" "${tag}" "${alias_tag}" "${arch:-${expected_arch}}" >&2
}

gido_flink_sql_runner_registry_ref() {
  local tag="${GIDO_IMAGE_TAG:-latest}"
  echo "registry.gido.svc.cluster.local:5000/gido-flink-sql-runner:${tag}"
}
