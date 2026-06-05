#!/usr/bin/env bash
# 排查谁占用了宿主 TCP 8081（Flink JM REST / Web UI 常用端口）。
# 不自动杀进程；看清输出后自行 kill <PID>、docker stop，或处理 K8s Service / port-forward。
set -euo pipefail
echo "=== 监听 TCP 8081（LISTEN）==="
if command -v lsof >/dev/null 2>&1; then
  lsof -nP -iTCP:8081 -sTCP:LISTEN 2>/dev/null || echo "(lsof 无输出：可能未被占用或权限不足)"
else
  echo "未安装 lsof，请用: netstat -anv | grep 8081  或  ss -lntp | grep 8081"
fi
echo ""
echo "=== Kubernetes：flink 命名空间（本仓库 k8s/flink.yaml 中 JM Service 为 LoadBalancer）==="
# Docker Desktop 上 LoadBalancer 常由 com.docker 在宿主监听 8081，与「无 compose 容器」可同时出现。
if command -v kubectl >/dev/null 2>&1; then
  if kubectl get ns flink >/dev/null 2>&1; then
    kubectl get svc -n flink -o wide 2>/dev/null || true
    echo ""
    echo "说明：若 flink-jobmanager 为 LoadBalancer 且 EXTERNAL-IP 为 localhost/127.0.0.1，"
    echo "      上面 lsof 出现 com.docker 占用 8081 通常表示「K8s 上的 JM 已暴露到本机」，属正常。"
    echo "      要腾出 8081 给本机 docker-compose Flink：可 kubectl delete -f <仓库根>/k8s/flink.yaml，"
    echo "      或把 JM Service 改为 ClusterIP + 用其它宿主端口做 kubectl port-forward。"
  else
    echo "(当前集群无 flink 命名空间，或未配置 kubeconfig / 未连上集群)"
  fi
else
  echo "(未安装 kubectl，跳过)"
fi
echo ""
echo "=== 本机 kubectl port-forward（若曾跑 k8s/port-forward-jobmanager.sh）==="
if command -v pgrep >/dev/null 2>&1; then
  pgrep -af kubectl 2>/dev/null | grep -E 'port-forward.*flink.*8081:8081|:8081:8081' || echo "(未发现匹配进程)"
else
  echo "(无 pgrep，可手动: ps aux | grep port-forward)"
fi
echo ""
echo "=== Docker 容器：发布宿主端口 8081（含已退出、仍占端口的异常情形）==="
if command -v docker >/dev/null 2>&1; then
  _pub8081=$(docker ps -a -q --filter "publish=8081" 2>/dev/null | tr -d '\n' || true)
  if [[ -n "${_pub8081}" ]]; then
    docker ps -a --filter "publish=8081" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
  else
    echo "(无 publish=8081 的容器；若 lsof 仍有 com.docker，多半为上面 K8s LoadBalancer 路径)"
  fi
else
  echo "未安装 docker"
fi
echo ""
echo "提示：仅 Docker Compose Flink 冲突时，先 bash scripts/stop-all-local-flink.sh 再启动 compose。"
echo "     本仓库 K8s 清单统一在仓库根目录 k8s/（如 flink.yaml、doris-fixed.yaml）。"
