#!/usr/bin/env bash
# =============================================================================
# 大数据平台一键编排：PostgreSQL + Kafka + Flink + DolphinScheduler + GIDO
#
# 用法（在仓库根目录 bigdata_all/）：
#   ./start-platform.sh              # 启动（等同 start）
#   ./start-platform.sh start        # 启动全栈
#   ./start-platform.sh stop           # 停止容器（保留卷）
#   ./start-platform.sh down           # 停止并删除容器（保留卷）
#   ./start-platform.sh restart        # down 后重新 start
#   ./start-platform.sh status         # 查看 compose ps
#   ./start-platform.sh logs [服务名]  # 跟踪日志，如 logs backend
#
# 启动选项：
#   --no-build      不重新 build
#   --recreate      强制重建容器
#   --pull          build 前拉基础镜像
#   --infra-only    仅基础设施，不启 GIDO
#   --reset-data    down -v 清卷后全量重起（开发重置）
#   --help
# =============================================================================
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose-platform.yml}"
DOCKERFILE_DIR="$ROOT_DIR/dockerFile"
JDBC_JAR="$DOCKERFILE_DIR/jdbc/mysql-connector-j-8.0.33.jar"
FLINK_KAFKA_JAR="$DOCKERFILE_DIR/flink-lib/flink-sql-connector-kafka-4.0.1-2.0.jar"

ACTION="start"
DO_BUILD=1
DO_RECREATE=0
DO_PULL=0
INFRA_ONLY=0
RESET_DATA=0
SKIP_HEALTH="${GIDO_SKIP_HEALTH_WAIT:-0}"

usage() {
  sed -n '3,22p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

if [[ $# -gt 0 && "$1" != --* ]]; then
  case "$1" in
    start|stop|down|restart|status|ps|logs|help)
      ACTION="$1"
      shift
      ;;
  esac
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) DO_BUILD=0 ;;
    --recreate) DO_RECREATE=1 ;;
    --pull) DO_PULL=1 ;;
    --infra-only) INFRA_ONLY=1 ;;
    --reset-data) RESET_DATA=1 ;;
    -h|--help) usage ;;
    *)
      if [[ "$ACTION" == "logs" ]]; then
        break
      fi
      echo "未知参数: $1（使用 --help 查看说明）" >&2
      exit 2
      ;;
  esac
  shift
done

log() { echo ">>> $*"; }
die() { echo "错误: $*" >&2; exit 1; }

if ! command -v docker >/dev/null 2>&1; then
  die "未检测到 docker，请先安装并启动 Docker。"
fi
if ! docker info >/dev/null 2>&1; then
  die "Docker daemon 未运行。"
fi
if ! docker compose version >/dev/null 2>&1; then
  die "需要 Docker Compose V2（docker compose）。"
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
  die "未找到 $COMPOSE_FILE，请在仓库根目录执行。"
fi

COMPOSE=(docker compose -f "$COMPOSE_FILE")

case "$ACTION" in
  stop)
    log "停止平台栈（保留数据卷，容器不删除）…"
    "${COMPOSE[@]}" stop
    echo ""
    "${COMPOSE[@]}" ps
    exit 0
    ;;
  down)
    log "停止并移除平台容器（默认保留数据卷）…"
    if [[ "$RESET_DATA" -eq 1 ]]; then
      log "同时删除 postgres / kafka 等数据卷…"
      "${COMPOSE[@]}" down -v --remove-orphans
    else
      "${COMPOSE[@]}" down --remove-orphans
    fi
    docker rm -f platform-ds-schema 2>/dev/null || true
    log "平台栈已 down"
    exit 0
    ;;
  status|ps)
    "${COMPOSE[@]}" ps
    exit 0
    ;;
  logs)
    log "日志（Ctrl+C 退出）…"
    exec "${COMPOSE[@]}" logs -f "$@"
    ;;
  restart)
    log "重启平台栈…"
    RESET_SAVE="$RESET_DATA"
    RESET_DATA=0
    "$0" down
    RESET_DATA="$RESET_SAVE"
    exec "$0" start \
      $( [[ "$DO_BUILD" -eq 0 ]] && echo --no-build ) \
      $( [[ "$DO_RECREATE" -eq 1 ]] && echo --recreate ) \
      $( [[ "$DO_PULL" -eq 1 ]] && echo --pull ) \
      $( [[ "$INFRA_ONLY" -eq 1 ]] && echo --infra-only ) \
      $( [[ "$RESET_DATA" -eq 1 ]] && echo --reset-data )
    ;;
  start|help)
    ;;
  *)
    die "未知命令: $ACTION（可用: start | stop | down | restart | status | logs）"
    ;;
esac

ensure_jar() {
  local dest="$1"
  local url="$2"
  local label="$3"
  if [[ -f "$dest" ]]; then
    log "已存在: $label"
    return 0
  fi
  log "下载 $label …"
  mkdir -p "$(dirname "$dest")"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$dest" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$dest" "$url"
  else
    die "缺少 $label，且本机无 curl/wget。请手动下载到: $dest"
  fi
}

ensure_jar "$JDBC_JAR" \
  "https://repo.maven.apache.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar" \
  "MySQL JDBC（Dolphin Worker/API）"

ensure_jar "$FLINK_KAFKA_JAR" \
  "https://repo.maven.apache.org/maven2/org/apache/flink/flink-sql-connector-kafka/4.0.1-2.0/flink-sql-connector-kafka-4.0.1-2.0.jar" \
  "Flink Kafka SQL 连接器"

if [[ -f "$ROOT_DIR/.env" ]]; then
  log "将加载根目录 .env（GIDO / Kafka 等）"
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
else
  echo "提示: 未找到 .env，使用 compose 内默认连接（同网 postgres / dolphin / flink）。"
  echo "      生产环境请复制 .env.example 为 .env 并填写 GIDO_DS_TOKEN 等。"
fi

# Kafka 外网 listener 广播地址：局域网 IP（DataGovRN / kafka-tool）或 host.docker.internal
resolve_kafka_lan_host() {
  if [[ -n "${KAFKA_LAN_HOST:-}" ]]; then
    return 0
  fi
  local ip=""
  for iface in en0 en1 en2; do
    ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
    [[ -n "$ip" ]] && break
  done
  if [[ -n "$ip" ]]; then
    export KAFKA_LAN_HOST="$ip"
  else
    export KAFKA_LAN_HOST="host.docker.internal"
  fi
}
resolve_kafka_lan_host
log "Kafka 外网地址（PLAINTEXT_HOST）: ${KAFKA_LAN_HOST}:9092  |  Docker 网内 Flink: kafka:29092"

stop_legacy_compose_stacks() {
  # 先停掉「单独 gido 项目」与旧分栈 compose，避免 gido-frontend / 3002 端口冲突
  if [[ -f "$ROOT_DIR/gido/docker-compose.yml" ]]; then
    log "停止单独 GIDO 编排（gido/docker-compose.yml）"
    docker compose -f "$ROOT_DIR/gido/docker-compose.yml" down --remove-orphans 2>/dev/null || true
  fi
  local f
  for f in \
    "$ROOT_DIR/dockerFile/docker-compose.flink.yml" \
    "$ROOT_DIR/dockerFile/docker-compose.dolphin.yml"
  do
    if [[ -f "$f" ]]; then
      log "停止旧编排: $f"
      docker compose -f "$f" down --remove-orphans 2>/dev/null || true
    fi
  done
  local c
  for c in gido-backend gido-frontend dataworks-backend dataworks-frontend; do
    if docker container inspect "$c" >/dev/null 2>&1; then
      log "移除残留容器: $c"
      docker rm -f "$c" >/dev/null 2>&1 || true
    fi
  done
}

cleanup_stale_gido_named_containers() {
  local pair c expect_svc svc
  for pair in "gido-backend:backend" "gido-frontend:frontend"; do
    c="${pair%%:*}"
    expect_svc="${pair##*:}"
    if ! docker container inspect "$c" >/dev/null 2>&1; then
      continue
    fi
    svc="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$c" 2>/dev/null || true)"
    if [[ "$svc" != "$expect_svc" ]] || [[ "$DO_RECREATE" -eq 1 ]]; then
      log "移除 GIDO 固定名容器 ${c}（当前 service=${svc:-无}，目标 service=${expect_svc}）"
      docker rm -f "$c" >/dev/null 2>&1 || true
    fi
  done
}

free_host_port_for_platform() {
  local port="$1"
  local cid project name
  while IFS= read -r cid; do
    [[ -z "$cid" ]] && continue
    project="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$cid" 2>/dev/null || true)"
    name="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's/^\///')"
    if [[ "$project" == "bigdata-platform" ]]; then
      continue
    fi
    log "释放端口 ${port}：停止 ${name}（project=${project:-standalone}）"
    docker rm -f "$cid" >/dev/null 2>&1 || true
  done < <(docker ps -aq --filter "publish=${port}" 2>/dev/null || true)
}

warn_if_port_still_busy() {
  local port="$1"
  local hint="$2"
  if command -v lsof >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "警告: 端口 ${port} 仍被占用（${hint}）"
    fi
  fi
}

stop_legacy_compose_stacks
  for _p in 8081 8083 12345 5432 9092 8001 3002 3003; do
  free_host_port_for_platform "$_p"
done
warn_if_port_still_busy 8081 "常见：旧 docker-compose Flink、kubectl port-forward、K8s JM LoadBalancer"

if [[ "$RESET_DATA" -eq 1 ]]; then
  log "重置平台数据卷（postgres / kafka 等，会清空 Dolphin 与 GIDO 元库）…"
  "${COMPOSE[@]}" down -v --remove-orphans 2>/dev/null || true
  docker rm -f platform-ds-schema 2>/dev/null || true
fi

UP_ARGS=(-d)
[[ "$DO_RECREATE" -eq 1 ]] && UP_ARGS+=(--force-recreate)

SERVICES=()
if [[ "$INFRA_ONLY" -eq 1 ]]; then
  SERVICES=(
    postgres zookeeper kafka
    ds-schema dolphinscheduler-api dolphinscheduler-master dolphinscheduler-worker dolphinscheduler-alert
    jobmanager taskmanager sql-gateway
  )
  log "仅启动基础设施（不含 GIDO）…"
else
  log "启动全栈（含 GIDO 前后端）…"
fi

if [[ "$DO_BUILD" -eq 1 ]]; then
  BUILD_ARGS=(build)
  [[ "$DO_PULL" -eq 1 ]] && BUILD_ARGS+=(--pull)
  if [[ "$INFRA_ONLY" -eq 1 ]]; then
    log "跳过 build（--infra-only 不含 GIDO 镜像）"
  else
    log "构建 GIDO 镜像…"
    "${COMPOSE[@]}" "${BUILD_ARGS[@]}" backend frontend
  fi
fi

if [[ "$INFRA_ONLY" -eq 0 ]]; then
  cleanup_stale_gido_named_containers
fi

if [[ ${#SERVICES[@]} -gt 0 ]]; then
  "${COMPOSE[@]}" up "${UP_ARGS[@]}" "${SERVICES[@]}"
else
  "${COMPOSE[@]}" up "${UP_ARGS[@]}"
fi

echo ""
log "容器状态:"
"${COMPOSE[@]}" ps
echo ""

verify_gido_frontend_port() {
  [[ "$INFRA_ONLY" -eq 1 ]] && return 0
  local ui_port="${GIDO_UI_PORT:-3002}"
  if ! docker container inspect gido-frontend >/dev/null 2>&1; then
    echo "警告: gido-frontend 未运行，但端口 ${ui_port} 可能仍被旧转发占用。"
    echo "      执行: bash scripts/reset-gido-docker.sh && ./start-platform.sh"
    return 0
  fi
  local in_c host
  in_c="$(docker exec gido-frontend sh -c "wget -qO- http://127.0.0.1/ 2>/dev/null | sed -n 's|.*src=\"/assets/\\(index-[^\"]*\\)\".*|\\1|p' | head -1" || true)"
  host="$(curl -sf --max-time 3 "http://127.0.0.1:${ui_port}/" 2>/dev/null | sed -n 's|.*src="/assets/\(index-[^"]*\)".*|\1|p' | head -1 || true)"
  if [[ -n "$in_c" && -n "$host" && "$in_c" != "$host" ]]; then
    echo "警告: 宿主机 ${ui_port} 与 gido-frontend 内容不一致（容器=${in_c}，宿主机=${host}）。"
    echo "      多为 OrbStack 端口转发残留 → 重启 OrbStack 后执行 bash scripts/reset-gido-docker.sh"
  elif [[ -n "$in_c" && "$in_c" == "$host" ]]; then
    log "GIDO 前端端口 ${ui_port} 与容器一致 (${in_c})"
  fi
}
verify_gido_frontend_port

BACKEND_URL="${GIDO_HEALTH_URL:-http://127.0.0.1:8001/health}"
if [[ "$INFRA_ONLY" -eq 0 && "$SKIP_HEALTH" != "1" ]]; then
  log "等待 GIDO 后端 ($BACKEND_URL)…"
  ok=0
  for _ in $(seq 1 60); do
    if curl -sf --max-time 3 "$BACKEND_URL" >/dev/null 2>&1; then
      ok=1
      break
    fi
    sleep 5
  done
  if [[ "$ok" -eq 1 ]]; then
    log "GIDO 后端就绪"
  else
    echo "警告: 后端在约 5 分钟内未响应 /health，可能仍在 init_db。"
    echo "      日志: ./start-platform.sh logs backend"
  fi
fi

if [[ "$INFRA_ONLY" -eq 0 ]]; then
  if ! docker ps --format '{{.Names}}' | grep -qx 'platform-kafka'; then
    echo "警告: Kafka 未运行（platform-kafka）。Flink SQL 连 Kafka 会失败。"
    echo "      排查: docker ps -a | grep kafka && docker logs platform-kafka"
    echo "      启动: docker compose -f $COMPOSE_FILE up -d kafka"
    echo "      若 CLUSTER_ID/卷损坏: docker compose -f $COMPOSE_FILE stop kafka && docker rm -f platform-kafka && docker volume rm \$(docker volume ls -q | grep kafka-data) && docker compose -f $COMPOSE_FILE up -d kafka"
  fi
fi

cat <<EOF

========================================
 平台服务已启动
========================================
  GIDO 前端   http://127.0.0.1:${GIDO_UI_PORT:-3002}
  GIDO 局域网 http://${KAFKA_LAN_HOST:-本机LAN_IP}:${GIDO_UI_PORT:-3002}  （需 GIDO_BIND_HOST=0.0.0.0，默认已开）
  GIDO API    http://127.0.0.1:${GIDO_API_PORT:-8001}/docs
  Dolphin UI       http://127.0.0.1:12345/dolphinscheduler/ui
  Flink Web UI     http://127.0.0.1:8081
  Flink Gateway    http://127.0.0.1:8083
  Kafka (Docker 内/Flink)  kafka:29092
  Kafka (局域网/宿主机)    ${KAFKA_LAN_HOST:-见启动日志}:9092

  常用:
    状态   ./start-platform.sh status
    日志   ./start-platform.sh logs backend
    停止   ./start-platform.sh stop
    下线   ./start-platform.sh down
    重启   ./start-platform.sh restart
    清卷   ./start-platform.sh down --reset-data
========================================
EOF
