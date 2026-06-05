#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RT="${ROOT}/dataease/runtime"
mkdir -p "${RT}/cache" "${RT}/logs"
mkdir -p "${RT}/data/static-resource" "${RT}/data/geo"
mkdir -p "${RT}/data/appearance" "${RT}/data/exportData" "${RT}/data/plugin"
mkdir -p "${RT}/data/font" "${RT}/data/i18n"
echo "OK: ${RT}"
if [[ -f "${ROOT}/dataease/.env" ]]; then
  bash "${ROOT}/dataease/render-conf.sh"
else
  echo "提示：cp dataease/.env.example dataease/.env 后执行 bash dataease/render-conf.sh"
fi
