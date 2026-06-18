#!/usr/bin/env bash
# 校验 gido-flink-runtime 镜像（按 runtime-versions.json 中的 verify 规则）
# 用法：
#   bash k8s/flink-runtime/scripts/verify-image.sh <镜像:tag> [runtime_key]
#   RUNTIME_VERSION=2.2.1 bash k8s/flink-runtime/scripts/verify-image.sh <镜像:tag>
set -euo pipefail

gido_python() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$@"
  else
    python "$@"
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${RUNTIME_DIR}/../.." && pwd)"
IMAGE="${1:?用法: verify-image.sh <镜像名:tag> [runtime_key]}"
RUNTIME_KEY="${2:-${RUNTIME_VERSION:-}}"

if [[ -z "${RUNTIME_KEY}" ]]; then
  RUNTIME_KEY="$(gido_python -c "import json; print(json.load(open('${RUNTIME_DIR}/runtime-versions.json'))['default'])")"
fi

MANIFEST="${REPO_ROOT}/k8s/flink-sql-runner/.build/${RUNTIME_KEY}/verify-manifest.json"
if [[ ! -f "${MANIFEST}" ]]; then
  gido_python "${RUNTIME_DIR}/scripts/render_build_context.py" "${RUNTIME_KEY}"
fi

echo "==> 校验镜像 ${IMAGE} (runtime ${RUNTIME_KEY})"

gido_python - "${MANIFEST}" "${IMAGE}" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
image = sys.argv[2]
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

checks = []
for glob in manifest.get("required_globs", []):
    checks.append(f'test -e "{glob}" || {{ echo "缺少: {glob}"; exit 1; }}')
    checks.append(f'echo "OK {glob}"')
for name in manifest.get("forbidden_files", []):
    checks.append(
        f'if test -f "/opt/flink/lib/{name}"; then echo "禁止存在: /opt/flink/lib/{name}"; exit 1; fi'
    )
checks.append("shopt -s nullglob")
for glob in manifest.get("forbidden_globs", []):
    checks.append(f"bad=({glob})")
    checks.append(f'if (("${{#bad[@]}}")); then echo "禁止: {glob}: ${{bad[*]}}"; exit 1; fi')
checks.extend([
    'CP="$(ls /opt/flink/lib/*.jar | paste -sd: -):/opt/flink/usrlib/sql-runner.jar"',
    'JAVA="${JAVA_HOME:-/opt/java/openjdk}/bin/java"',
    'test -x "${JAVA}" || JAVA="$(command -v java)"',
    'if ! "${JAVA}" -cp "${CP}" com.gido.flink.RuntimeSmoke >/dev/null 2>&1; then',
    '  echo "RuntimeSmoke 失败（Configuration / CatalogContext 初始化）"',
    '  "${JAVA}" -cp "${CP}" com.gido.flink.RuntimeSmoke || true',
    "  exit 1",
    "fi",
    'echo "OK RuntimeSmoke: CatalogContext + ParquetReadOptions"',
])

script = "\n".join(["set -euo pipefail"] + checks)
subprocess.run(
    ["docker", "run", "--rm", image, "bash", "-c", script],
    check=True,
)
PY

echo "==> 镜像校验通过: ${IMAGE}"
