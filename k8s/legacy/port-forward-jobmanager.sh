#!/usr/bin/env bash

# 将集群内 flink-jobmanager 的 8081 转到宿主 8081（需本机 8081 未被占用）
set -euo pipefail
while true; do
  echo "[$(date)] port-forward flink/flink-jobmanager 8081:8081 ..."
  kubectl port-forward -n flink svc/flink-jobmanager 8081:8081 --address=0.0.0.0 || true
  echo "[$(date)] 断开，3 秒后重试..."
  sleep 3
done
