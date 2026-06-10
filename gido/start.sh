#!/usr/bin/env bash

# =============================================================================
# 玑渡 GIDO 一键启动（Docker Compose：backend + frontend）
#
# 自动处理：
#   - 检查 Docker 是否安装且 daemon 已运行
#   - 检查 Docker 是否安装且 daemon 已运行
#   - 可选：检查全栈网络 bigdata-platform-network 是否存在
#   - build + up -d，并等待后端 /health（可选跳过）
#
# 用法：
#   ./start.sh                 # 默认：建网 + build + 启动
#   ./start.sh --no-build      # 不重新 build，仅 up
#   ./start.sh --recreate      # 强制重建容器（改代码/环境变量后常用）
#   ./start.sh --pull          # build 前拉取基础镜像
#   ./start.sh --help
#
# 环境变量（可选）：
#   DS_PLATFORM_NETWORK        外部网络名，默认 bigdata-platform-network（与全栈 compose 一致）
#   GIDO_SKIP_HEALTH_WAIT=1  不等待后端健康检查
#   GIDO_ENV_FILE           上级 .env 路径，默认 ../.env
#
# 前置：上一级目录 ../.env（可选，用于 DATABASE_URL / Dolphin / Flink）
# 元库：默认连 host.docker.internal:5432 库 gido（与 Dolphin PG 同机时）
# =============================================================================
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
# 勿用 ENV_FILE：易与 shell/工具环境变量冲突，且在 set -u 下部分 bash 会报 unbound
ROOT_ENV_FILE="../.env"
if [[ -n "${GIDO_ENV_FILE:-}" ]]; then
  ROOT_ENV_FILE="${GIDO_ENV_FILE}"
fi
NET="${DS_PLATFORM_NETWORK:-bigdata-platform-network}"
UI_PORT="${GIDO_UI_PORT:-3002}"
API_PORT="${GIDO_API_PORT:-8001}"

DO_BUILD=1
DO_RECREATE=0
DO_PULL=0
SKIP_HEALTH="${GIDO_SKIP_HEALTH_WAIT:-0}"

usage() {
  sed -n '3,22p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) DO_BUILD=0 ;;
    --recreate) DO_RECREATE=1 ;;
    --pull) DO_PULL=1 ;;
    -h|--help) usage ;;
    *)
      echo "未知参数: $1（使用 --help 查看说明）" >&2
      exit 2
      ;;
  esac
  shift
done

log() { echo ">>> $*"; }
die() { echo "错误: $*" >&2; exit 1; }

# --- Docker ---
if ! command -v docker >/dev/null 2>&1; then
  die "未检测到 docker，请先安装并启动 Docker Desktop。"
fi
if ! docker info >/dev/null 2>&1; then
  die "Docker daemon 未运行，请先打开 Docker Desktop。"
fi
if ! docker compose version >/dev/null 2>&1; then
  die "未检测到 docker compose（需 Docker Compose V2）。"
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
  die "未找到 $COMPOSE_FILE，请在 gido 目录下执行本脚本。"
fi

# --- 外部网络（可选：与旧版 Dolphin 分栈 compose 互通时使用）---
if docker network inspect "$NET" >/dev/null 2>&1; then
  log "Docker 网络已存在: $NET"
else
  log "提示: 外部网络 $NET 不存在（当前 gido/docker-compose.yml 默认不依赖该网络；全栈请用 ../start-platform.sh）"
fi

# --- .env 提示 ---
if [[ -f "$ROOT_ENV_FILE" ]]; then
  log "已加载环境文件: ${ROOT_ENV_FILE} (compose env_file)"
else
  echo "提示: 未找到 ${ROOT_ENV_FILE}，将使用 compose 内 DATABASE_URL 等默认值。"
  echo "      生产或自定义 Dolphin/Flink/库 连接时，建议在仓库根目录创建 .env。"
fi

# --- Compose ---
COMPOSE=(docker compose -f "$COMPOSE_FILE")
if [[ -f "$ROOT_ENV_FILE" ]]; then
  COMPOSE+=(--env-file "$ROOT_ENV_FILE")
fi
UP_ARGS=(-d)
[[ "$DO_RECREATE" -eq 1 ]] && UP_ARGS+=(--force-recreate)

if [[ "$DO_BUILD" -eq 1 ]]; then
  BUILD_ARGS=(build)
  [[ "$DO_PULL" -eq 1 ]] && BUILD_ARGS+=(--pull)
  log "构建镜像…"
  "${COMPOSE[@]}" "${BUILD_ARGS[@]}"
fi

log "启动服务 backend + frontend…"
"${COMPOSE[@]}" up "${UP_ARGS[@]}"

# --- 状态 ---
echo ""
log "容器状态:"
"${COMPOSE[@]}" ps
echo ""

# --- 等待后端健康 ---
BACKEND_URL="${GIDO_HEALTH_URL:-http://127.0.0.1:8001/health}"
if [[ "$SKIP_HEALTH" != "1" ]]; then
  log "等待后端就绪 ($BACKEND_URL)…"
  ok=0
  for i in $(seq 1 60); do
    if curl -sf --max-time 3 "$BACKEND_URL" >/dev/null 2>&1; then
      ok=1
      break
    fi
    # 首轮 init_db 可能较慢
    sleep 5
  done
  if [[ "$ok" -eq 1 ]]; then
    log "后端健康检查通过"
  else
    echo "警告: 后端在约 5 分钟内未响应 /health，可能仍在 init_db 或数据库不可达。"
    echo "      查看日志: docker compose -f $COMPOSE_FILE logs -f backend"
  fi
fi

cat <<EOF

========================================
 GIDO 已执行启动命令
========================================
  前端:  http://127.0.0.1:${UI_PORT}
  后端:  http://127.0.0.1:${API_PORT}
  文档:  http://127.0.0.1:${API_PORT}/docs
  健康:  http://127.0.0.1:${API_PORT}/health

  全栈（PG/Kafka/Flink/Dolphin）请用仓库根目录: ../start-platform.sh
  勿与全栈同时启动，container_name 均为 gido-backend / gido-frontend。

  默认管理员（首次 bootstrap）: admin / 见 GIDO_BOOTSTRAP_ADMIN_PASSWORD（默认 admin123）

  常用命令:
    查看日志:  docker compose -f $COMPOSE_FILE logs -f backend
    停止:      docker compose -f $COMPOSE_FILE down
    重建启动:  ./start.sh --recreate

  若库连不上: 确认 PostgreSQL 在宿主机 5432 可达，或在上级 .env 设置 GIDO_DATABASE_URL
  Dolphin（可选）: 仓库根目录 ./start-platform.sh
========================================
EOF
