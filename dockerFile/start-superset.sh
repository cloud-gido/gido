#!/usr/bin/env bash
# Superset 一键启动（默认复用 Dolphin 的 PG + 本机/容器 Redis）
#
# 用法：
#   ./start-superset.sh              # 复用 Dolphin PG（需已 up）+ 检查 Redis
#   ./start-superset.sh --bundled    # 自带 PG(5433)+Redis，不依赖 Dolphin 网络
#   ./start-superset.sh --no-build
#   ./start-superset.sh --help
#
# UI: http://127.0.0.1:8088  默认 admin / admin
set -eo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

COMPOSE_BASE="docker-compose.superset.yml"
COMPOSE_BUNDLED="docker-compose.superset.bundled.yml"
ENV_FILE="./superset/.env"
USE_BUNDLED=0

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundled) USE_BUNDLED=1 ;;
    -h|--help) usage ;;
    *)
      echo "未知参数: $1" >&2
      exit 2
      ;;
  esac
  shift
done

log() { echo ">>> $*"; }
die() { echo "错误: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "未安装 docker"
docker info >/dev/null 2>&1 || die "Docker daemon 未运行"
docker compose version >/dev/null 2>&1 || die "需要 docker compose V2"

if [[ ! -f "$ENV_FILE" ]]; then
  log "生成 ${ENV_FILE}（从 .env.example）"
  cp ./superset/.env.example "$ENV_FILE"
fi

COMPOSE=(docker compose -f "$COMPOSE_BASE")
if [[ "$USE_BUNDLED" -eq 1 ]]; then
  log "模式: bundled（自带 PostgreSQL + Redis）"
  COMPOSE+=(-f "$COMPOSE_BUNDLED")
else
  log "模式: 复用 Dolphin PostgreSQL（网络见 superset/.env SUPERSET_DOCKER_NETWORK）"
  NET="$(grep -E '^SUPERSET_DOCKER_NETWORK=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
  NET="${NET:-dolphinscheduler-docker_default}"
  if ! docker network inspect "$NET" >/dev/null 2>&1; then
    die "Docker 网络不存在: ${NET}。请先启动 Dolphin compose，或改用 ./start-superset.sh --bundled"
  fi
  log "准备 Superset 元库 superset …"
  bash ./superset/prepare-external-pg.sh || die "建库失败，见上方 psql 输出"

  REDIS_HOST="$(grep -E '^REDIS_HOST=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r' || echo host.docker.internal)"
  REDIS_PORT="$(grep -E '^REDIS_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r' || echo 6379)"
  if [[ "$REDIS_HOST" == "host.docker.internal" ]]; then
    if ! (echo >/dev/tcp/127.0.0.1/"$REDIS_PORT") 2>/dev/null; then
      echo ""
      echo "警告: 宿主机 ${REDIS_PORT} 无 Redis 监听，superset-init 会在约 2 分钟后因 Redis 超时失败。"
      echo "任选其一："
      echo "  1) 启动 Redis: docker run -d --name superset-redis -p 6379:6379 docker.m.daocloud.io/library/redis:7.4-alpine"
      echo "  2) 使用自带 Redis: ./start-superset.sh --bundled"
      echo ""
      read -r -p "仍要继续? [y/N] " ans || true
      [[ "${ans:-n}" =~ ^[Yy]$ ]] || exit 1
    else
      log "宿主机 Redis ${REDIS_PORT} 可连接"
    fi
  fi
fi

log "启动 Superset（init 首次约 1–3 分钟）…"
"${COMPOSE[@]}" up -d

echo ""
log "若 init 失败，查看日志:"
echo "  docker logs superset-docker-superset-init-1"
echo ""
log "成功后访问: http://127.0.0.1:8088 （用户/密码见 superset/.env）"
