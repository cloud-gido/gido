#!/usr/bin/env bash

# 清理 GIDO Docker 端口冲突与 OrbStack 残留转发（3002 / 8001）
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

UI_PORT="${GIDO_UI_PORT:-3002}"
API_PORT="${GIDO_API_PORT:-8001}"

log() { echo ">>> $*"; }

if ! docker info >/dev/null 2>&1; then
  echo "错误: Docker 未运行" >&2
  exit 1
fi

log "停止单独 GIDO compose 项目…"
docker compose -f gido/docker-compose.yml down --remove-orphans 2>/dev/null || true

log "停止全栈 compose 中的 GIDO 服务…"
docker compose -f docker-compose-platform.yml stop frontend backend 2>/dev/null || true

for c in gido-frontend gido-backend; do
  if docker container inspect "$c" >/dev/null 2>&1; then
    log "移除容器: $c"
    docker rm -f "$c" >/dev/null 2>&1 || true
  fi
done

for port in "$UI_PORT" "$API_PORT"; do
  while IFS= read -r cid; do
    [[ -z "$cid" ]] && continue
    name="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's/^\///')"
    log "释放端口 ${port}: 停止 ${name:-$cid}"
    docker rm -f "$cid" >/dev/null 2>&1 || true
  done < <(docker ps -aq --filter "publish=${port}" 2>/dev/null || true)
done

log "当前占用 ${UI_PORT}/${API_PORT} 的容器（应为空）:"
docker ps --format 'table {{.Names}}\t{{.Ports}}' | grep -E "${UI_PORT}|${API_PORT}|NAMES" || true

if curl -sf --max-time 2 "http://127.0.0.1:${UI_PORT}/" >/dev/null 2>&1; then
  echo ""
  echo "警告: 端口 ${UI_PORT} 仍可访问，但 gido-frontend 可能已删除 —— 多为 OrbStack 转发残留。"
  echo "      请菜单重启 OrbStack，然后执行: ./start-platform.sh"
  echo ""
  curl -s "http://127.0.0.1:${UI_PORT}/" | grep -E 'assets/index|main.tsx' || true
else
  log "端口 ${UI_PORT} 已空闲，可执行 ./start-platform.sh"
fi
