#!/usr/bin/env bash
# 兼容入口：转发至 k8s/flink-runtime/scripts/verify-image.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "${ROOT}/flink-runtime/scripts/verify-image.sh" "$@"
