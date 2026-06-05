#!/usr/bin/env bash
# DolphinScheduler 库表初始化（供 platform / dolphin compose 挂载）
# 镜像 ENTRYPOINT 已是 bash，compose 中 command 写: ["/opt/dolphinscheduler/tools/bin/ds-schema-init.sh"]
set -uo pipefail

cd /opt/dolphinscheduler || exit 1

export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Xms128m -Xmx768m -XX:+UseG1GC -XX:+ExitOnOutOfMemoryError"

pg_host="${PG_HOST:-platform-postgres}"
pg_port="${PG_PORT:-5432}"

echo "[ds-schema] waiting for ${pg_host}:${pg_port} (DNS + TCP) …"
ready=0
for i in $(seq 1 45); do
  if getent hosts "${pg_host}" >/dev/null 2>&1 \
    && bash -c "exec 3<>/dev/tcp/${pg_host}/${pg_port}" 2>/dev/null; then
    ready=1
    break
  fi
  echo "[ds-schema] not ready (${i}/45): cannot reach ${pg_host}:${pg_port}"
  sleep 2
done
if [[ "$ready" -ne 1 ]]; then
  echo "[ds-schema] ERROR: ${pg_host} unreachable from this container (check compose network)" >&2
  getent hosts "${pg_host}" 2>&1 || echo "[ds-schema] getent: no DNS entry for ${pg_host}" >&2
  exit 1
fi

max_attempts="${DS_SCHEMA_MAX_ATTEMPTS:-5}"
attempt=1

while [[ "$attempt" -le "$max_attempts" ]]; do
  echo "[ds-schema] upgrade-schema attempt ${attempt}/${max_attempts} …"
  if tools/bin/upgrade-schema.sh; then
    echo "[ds-schema] schema ready"
    exit 0
  fi
  rc=$?
  echo "[ds-schema] upgrade-schema exited ${rc}"
  if [[ "$attempt" -lt "$max_attempts" ]]; then
    sleep "$((attempt * 3))"
  fi
  attempt=$((attempt + 1))
done

cat <<'EOF' >&2
[ds-schema] FAILED after all retries.

常见原因：
  1) postgres 数据卷已有半初始化/旧版 Dolphin 表结构 → 开发环境可重置卷：
       ./start-platform.sh --reset-data
  2) Docker 内存不足导致 Java 无法启动 → 增大 Docker Desktop 内存至 ≥8GB
  3) ds-schema 未加入 platform 网络（UnknownHostException: postgres）→ 勿单独 docker run；
     使用 ./start-platform.sh 或 docker compose -f docker-compose-platform.yml up ds-schema

查看详细日志：
  docker logs platform-ds-schema
EOF
exit 1
