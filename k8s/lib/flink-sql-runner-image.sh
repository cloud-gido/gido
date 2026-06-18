

# GIDO 统一 Flink 运行时镜像（sql-runner + Paimon + CDC，Operator 唯一提交路径）
# 镜像名：gido-flink-sql-runner（主）/ gido-flink-runtime（别名）
# 版本配置：k8s/flink-runtime/runtime-versions.json（索引）+ k8s/flink-runtime/<flink_version>/
# 须先 source k8s/lib/kind-image.sh（平台由 gido_detect_build_platform 自动判断）

gido_python() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$@"
  else
    python "$@"
  fi
}

gido_flink_runtime_config_path() {
  local root="${1:?root dir}"
  echo "${root}/k8s/flink-runtime/runtime-versions.json"
}

gido_flink_runtime_default_key() {
  local root="${1:?root dir}"
  gido_python -c "import json; print(json.load(open('$(gido_flink_runtime_config_path "${root}")'))['default'])"
}

gido_flink_runtime_flink_version() {
  local root="${1:?root dir}"
  local key="${2:-$(gido_flink_runtime_default_key "${root}")}"
  gido_python "${root}/k8s/flink-runtime/scripts/render_build_context.py" --print-flink-version "${key}"
}

gido_flink_runtime_base_image() {
  local root="${1:?root dir}"
  local key="${2:-$(gido_flink_runtime_default_key "${root}")}"
  gido_python "${root}/k8s/flink-runtime/scripts/render_build_context.py" --print-base-image "${key}"
}

gido_flink_runtime_render() {
  local root="${1:?root dir}"
  local key="${2:-$(gido_flink_runtime_default_key "${root}")}"
  gido_python "${root}/k8s/flink-runtime/scripts/render_build_context.py" "${key}"
}

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
  local runtime_key="${4:-${GIDO_FLINK_RUNTIME_VERSION:-${RUNTIME_VERSION:-}}}"
  local context="${root}/k8s/flink-sql-runner"

  if [[ -z "${runtime_key}" ]]; then
    runtime_key="$(gido_flink_runtime_default_key "${root}")"
  fi

  if [[ ! -f "${context}/Dockerfile" ]]; then
    echo "错误：未找到 ${context}/Dockerfile" >&2
    return 1
  fi

  gido_flink_runtime_render "${root}" "${runtime_key}" || return 1

  local build_dir="${context}/.build/${runtime_key}"
  if [[ ! -f "${build_dir}/connectors-pom.xml" ]]; then
    echo "错误：render 未生成 ${build_dir}" >&2
    return 1
  fi

  local flink_version sql_runner_version flink_base maven_image
  flink_version="$(gido_flink_runtime_flink_version "${root}" "${runtime_key}")"
  sql_runner_version="$(gido_python -c "import json; print(json.load(open('$(gido_flink_runtime_config_path "${root}")'))['sql_runner_artifact_version'])")"
  flink_base="${FLINK_BASE_IMAGE:-docker.m.daocloud.io/$(gido_flink_runtime_base_image "${root}" "${runtime_key}")}"
  maven_image="${MAVEN_IMAGE:-docker.m.daocloud.io/library/maven:3.9-eclipse-temurin-11}"

  local expected_arch
  expected_arch="$(platform_to_arch "${platform}")"
  printf '[%s] 构建 Flink 运行时 %s | profile %s (Flink %s) | 平台 %s（来源: %s）…\n' \
    "$(date '+%H:%M:%S')" "${tag}" "${runtime_key}" "${flink_version}" "${platform}" "$(gido_detect_build_platform_source)" >&2
  docker pull --platform "${platform}" "${flink_base}" >/dev/null 2>&1 || true
  docker pull --platform "${platform}" "${maven_image}" >/dev/null 2>&1 || true
  local build_args=(
    --build-arg "TARGETPLATFORM=${platform}"
    --build-arg "RUNTIME_PROFILE=${runtime_key}"
    --build-arg "FLINK_BASE_IMAGE=${flink_base}"
    --build-arg "SQL_RUNNER_ARTIFACT_VERSION=${sql_runner_version}"
    --build-arg "MAVEN_IMAGE=${maven_image}"
  )
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
      "${build_args[@]}" \
      -t "${tag}" \
      "${context}"
  else
    docker build \
      "${build_args[@]}" \
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
  printf '[%s] Flink 运行时构建完成: %s（别名 %s，runtime %s，架构 %s）\n' \
    "$(date '+%H:%M:%S')" "${tag}" "${alias_tag}" "${runtime_key}" "${arch:-${expected_arch}}" >&2
}

gido_flink_sql_runner_registry_ref() {
  local tag="${GIDO_IMAGE_TAG:-latest}"
  echo "registry.gido.svc.cluster.local:5000/gido-flink-sql-runner:${tag}"
}
