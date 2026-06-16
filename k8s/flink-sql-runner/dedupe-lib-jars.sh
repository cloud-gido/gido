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
    base="$(basename "$j")"
    [[ "$base" == "$keep" ]] && continue
    # 避免 netty-codec 误匹配 netty-codec-http 等同前缀 artifact
    rest="${base#${artifact}-}"
    rest="${rest%.jar}"
    [[ "$rest" =~ ^[0-9] ]] || continue
    echo "dedupe: remove ${base} (keep ${keep})"
    rm -f "$j"
  done
done < "${OVERRIDES_FILE}"

echo "dedupe: done in ${LIB_DIR} using ${OVERRIDES_FILE}"
