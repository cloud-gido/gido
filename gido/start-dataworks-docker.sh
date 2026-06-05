#!/usr/bin/env bash
# 兼容旧入口：转发到 start.sh
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/start.sh" "$@"
