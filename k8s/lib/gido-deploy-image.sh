# GIDO 分层部署策略：应用层每次发版更新；Flink 运行时按需更新。
# 配置：k8s/gido-deploy.env（见 gido-deploy.env.example）

gido_deploy_log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

gido_load_deploy_config() {
  local root="$1"
  local f="${GIDO_DEPLOY_ENV:-${root}/k8s/gido-deploy.env}"
  if [[ -f "${f}" ]]; then
    gido_deploy_log "加载部署配置 ${f}"
    # shellcheck disable=SC1090
    source "${f}"
  else
    gido_deploy_log "未找到 ${f}，使用内置默认（应用 always+fresh，Flink auto+stable）"
  fi

  GIDO_DEPLOY_APP_BUILD="${GIDO_DEPLOY_APP_BUILD:-always}"
  GIDO_DEPLOY_APP_TAG="${GIDO_DEPLOY_APP_TAG:-fresh}"
  GIDO_DEPLOY_APP_PULL="${GIDO_DEPLOY_APP_PULL:-Always}"
  GIDO_DEPLOY_FLINK_BUILD="${GIDO_DEPLOY_FLINK_BUILD:-auto}"
  GIDO_DEPLOY_FLINK_TAG="${GIDO_DEPLOY_FLINK_TAG:-stable}"
  GIDO_FLINK_RUNTIME_STABLE_TAG="${GIDO_FLINK_RUNTIME_STABLE_TAG:-flink-runtime}"
  GIDO_DEPLOY_FLINK_PULL="${GIDO_DEPLOY_FLINK_PULL:-IfNotPresent}"
}

gido_resolve_app_tag() {
  case "${GIDO_DEPLOY_APP_TAG}" in
    fresh)
      echo "app-$(date +%Y%m%d-%H%M%S)"
      ;;
    fixed)
      if [[ -z "${GIDO_APP_IMAGE_TAG:-}" ]]; then
        echo "错误：GIDO_DEPLOY_APP_TAG=fixed 须设置 GIDO_APP_IMAGE_TAG" >&2
        return 1
      fi
      echo "${GIDO_APP_IMAGE_TAG}"
      ;;
    *)
      echo "错误：未知 GIDO_DEPLOY_APP_TAG=${GIDO_DEPLOY_APP_TAG}" >&2
      return 1
      ;;
  esac
}

gido_resolve_flink_tag() {
  case "${GIDO_DEPLOY_FLINK_TAG}" in
    stable)
      echo "${GIDO_FLINK_RUNTIME_STABLE_TAG}"
      ;;
    fresh)
      echo "flink-$(date +%Y%m%d-%H%M%S)"
      ;;
    fixed)
      if [[ -z "${GIDO_FLINK_IMAGE_TAG:-}" ]]; then
        echo "错误：GIDO_DEPLOY_FLINK_TAG=fixed 须设置 GIDO_FLINK_IMAGE_TAG" >&2
        return 1
      fi
      echo "${GIDO_FLINK_IMAGE_TAG}"
      ;;
    *)
      echo "错误：未知 GIDO_DEPLOY_FLINK_TAG=${GIDO_DEPLOY_FLINK_TAG}" >&2
      return 1
      ;;
  esac
}

gido_flink_runtime_source_hash() {
  local root="$1"
  local dir="${root}/k8s/flink-sql-runner"
  [[ -d "${dir}" ]] || return 1
  find "${dir}" -type f \( \
    -name '*.java' -o -name '*.xml' -o -name 'Dockerfile' -o -name 'settings.xml' -o -name '*.manifest' \
  \) -print0 2>/dev/null | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | awk '{print $1}'
}

gido_flink_runtime_hash_file() {
  local root="$1"
  echo "${root}/k8s/.flink-runtime-content-hash"
}

gido_should_build_flink() {
  local root="$1"
  if [[ "${GIDO_FORCE_REBUILD_FLINK:-}" == "1" ]]; then
    gido_deploy_log "Flink 运行时：强制构建（GIDO_FORCE_REBUILD_FLINK=1）"
    return 0
  fi
  case "${GIDO_DEPLOY_FLINK_BUILD}" in
    always)
      gido_deploy_log "Flink 运行时：每次构建（GIDO_DEPLOY_FLINK_BUILD=always）"
      return 0
      ;;
    never)
      gido_deploy_log "Flink 运行时：跳过构建（GIDO_DEPLOY_FLINK_BUILD=never）"
      return 1
      ;;
    auto)
      local hash_file current stored
      hash_file="$(gido_flink_runtime_hash_file "${root}")"
      current="$(gido_flink_runtime_source_hash "${root}")"
      stored=""
      [[ -f "${hash_file}" ]] && stored="$(cat "${hash_file}")"
      if [[ "${current}" != "${stored}" ]]; then
        gido_deploy_log "Flink 运行时：源码有变更，将构建（hash ${current:0:12}…）"
        return 0
      fi
      gido_deploy_log "Flink 运行时：源码未变，跳过构建（使用稳定 tag ${GIDO_FLINK_RUNTIME_STABLE_TAG}）"
      return 1
      ;;
    *)
      echo "错误：未知 GIDO_DEPLOY_FLINK_BUILD=${GIDO_DEPLOY_FLINK_BUILD}" >&2
      return 1
      ;;
  esac
}

gido_record_flink_runtime_hash() {
  local root="$1"
  local hash
  hash="$(gido_flink_runtime_source_hash "${root}")"
  printf '%s\n' "${hash}" >"$(gido_flink_runtime_hash_file "${root}")"
}

gido_should_build_app() {
  case "${GIDO_DEPLOY_APP_BUILD}" in
    always) return 0 ;;
    never)
      if [[ "${GIDO_ALLOW_SKIP_BUILD:-}" == "1" ]]; then
        gido_deploy_log "警告：跳过应用构建（GIDO_DEPLOY_APP_BUILD=never）"
        return 1
      fi
      echo "错误：GIDO_DEPLOY_APP_BUILD=never 须设置 GIDO_ALLOW_SKIP_BUILD=1" >&2
      return 1
      ;;
    *)
      echo "错误：未知 GIDO_DEPLOY_APP_BUILD=${GIDO_DEPLOY_APP_BUILD}" >&2
      return 1
      ;;
  esac
}

gido_require_app_build_or_allow() {
  if [[ "${GIDO_SKIP_BUILD:-}" == "1" ]]; then
    echo "错误：GIDO_SKIP_BUILD 已废弃，请用 k8s/gido-deploy.env 配置 GIDO_DEPLOY_APP_BUILD / GIDO_DEPLOY_FLINK_BUILD" >&2
    [[ "${GIDO_ALLOW_SKIP_BUILD:-}" == "1" ]] || exit 1
  fi
}

gido_ensure_local_flink_image() {
  local tag="$1"
  local local_name="gido-flink-sql-runner:${tag}"
  if docker image inspect "${local_name}" >/dev/null 2>&1; then
    return 0
  fi
  for alt in "gido-flink-runtime:${tag}" "gido-flink-sql-runner:orbstack" "gido-flink-runtime:orbstack"; do
    if docker image inspect "${alt}" >/dev/null 2>&1; then
      gido_deploy_log "本地标记 ${alt} → ${local_name}"
      docker tag "${alt}" "${local_name}"
      return 0
    fi
  done
  echo "错误：无 Flink 运行时本地镜像 ${local_name}。请设置 GIDO_DEPLOY_FLINK_BUILD=always 或 GIDO_FORCE_REBUILD_FLINK=1 构建一次。" >&2
  return 1
}

gido_deploy_print_plan() {
  local root="$1" app_tag="$2" flink_tag="$3" build_flink="$4"
  gido_deploy_log "── 部署计划 ──"
  gido_deploy_log "  应用层: build=${GIDO_DEPLOY_APP_BUILD} tag=${app_tag} pull=${GIDO_DEPLOY_APP_PULL}"
  if [[ "${build_flink}" == "1" ]]; then
    gido_deploy_log "  运行时: 本次构建 tag=${flink_tag} pull=${GIDO_DEPLOY_FLINK_PULL}"
  else
    gido_deploy_log "  运行时: 跳过构建，使用 tag=${flink_tag} pull=${GIDO_DEPLOY_FLINK_PULL}"
  fi
  gido_deploy_log "────────────────"
}

gido_record_deploy_tags() {
  local root="$1" app_tag="$2" flink_tag="$3"
  printf 'app=%s\nflink=%s\n' "${app_tag}" "${flink_tag}" >"${root}/k8s/.last-deploy-image-tag"
  gido_deploy_log "本次 tag 已写入 k8s/.last-deploy-image-tag"
}
