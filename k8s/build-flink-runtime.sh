#!/usr/bin/env bash

# 本机构建 gido-flink-sql-runner / gido-flink-runtime（与 deploy 脚本同平台）
#
#   bash k8s/build-flink-runtime.sh
#   GIDO_IMAGE_TAG=orbstack bash k8s/build-flink-runtime.sh
#   GIDO_FLINK_RUNTIME_VERSION=2.0.1 bash k8s/build-flink-runtime.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/kind-image.sh
source "${ROOT}/k8s/lib/kind-image.sh"
# shellcheck source=lib/flink-sql-runner-image.sh
source "${ROOT}/k8s/lib/flink-sql-runner-image.sh"

PLATFORM="$(gido_detect_build_platform)"
TAG="${GIDO_IMAGE_TAG:-orbstack}"
RUNTIME_KEY="${GIDO_FLINK_RUNTIME_VERSION:-${RUNTIME_VERSION:-$(gido_flink_runtime_default_key "${ROOT}")}}"
LOCAL_TAG="gido-flink-sql-runner:${TAG}"

echo "==> 构建平台 ${PLATFORM}（来源: $(gido_detect_build_platform_source)）"
echo "    runtime profile: ${RUNTIME_KEY} (Flink $(gido_flink_runtime_flink_version "${ROOT}" "${RUNTIME_KEY}"))"
echo "    覆盖: export GIDO_BUILD_PLATFORM=linux/amd64  # 或 linux/arm64"
echo "    覆盖: export GIDO_FLINK_RUNTIME_VERSION=2.0.1  # 非默认 Flink 版本"
gido_flink_sql_runner_build "${PLATFORM}" "${LOCAL_TAG}" "${ROOT}" "${RUNTIME_KEY}"
echo "完成: ${LOCAL_TAG} + $(gido_flink_runtime_alias_tag "${LOCAL_TAG}")"
