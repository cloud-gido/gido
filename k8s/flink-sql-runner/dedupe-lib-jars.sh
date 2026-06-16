#!/usr/bin/env bash
# 按 Maven 坐标（group:artifact:version）在 lib 目录只保留指定版本 jar
set -euo pipefail

LIB_DIR="${1:?lib dir}"
OVERRIDES_FILE="${2:?overrides file (group:artifact:version per line)}"
shopt -s nullglob

while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"
  line="$(echo "$line" | xargs)"
  [[ -z "$line" ]] && continue
  IFS=':' read -r _group artifact version <<< "$line"
  [[ -n "$artifact" && -n "$version" ]] || continue
  keep="${artifact}-${version}.jar"
  for j in "${LIB_DIR}/${artifact}-"*.jar; do
    [[ -f "$j" ]] || continue
    if [[ "$(basename "$j")" != "$keep" ]]; then
      echo "dedupe: remove $(basename "$j") (keep ${keep})"
      rm -f "$j"
    fi
  done
done < "${OVERRIDES_FILE}"

echo "dedupe: done in ${LIB_DIR} using ${OVERRIDES_FILE}"
