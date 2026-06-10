#!/usr/bin/env bash

# 将 flink 命名空间下的 Session 集群与本脚本同目录的 flink.yaml 对齐（镜像、Deployment），并可选 apply Ingress/NodePort。
# 若曾只执行 kubectl delete deploy flink-jobmanager flink-taskmanager flink-sql-gateway -n flink：
#   Namespace/Service 往往仍在；kubectl apply -f flink.yaml 会重建 Deployment；本脚本会 rollout restart 以拉齐 Pod。
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="${DIR}/flink.yaml"
echo ">>> 使用清单: ${MANIFEST}"
kubectl apply -f "${MANIFEST}"
for extra in flink-sql-gateway-ingress.yaml flink-sql-gateway-nodeport.yaml; do
  xp="${DIR}/${extra}"
  if [[ -f "$xp" ]]; then
    echo ">>> apply 可选: ${extra}"
    kubectl apply -f "${xp}" || true
  fi
done
kubectl rollout restart deployment/flink-jobmanager deployment/flink-taskmanager deployment/flink-sql-gateway -n flink
kubectl rollout status deployment/flink-jobmanager -n flink --timeout=300s
kubectl rollout status deployment/flink-taskmanager -n flink --timeout=300s
kubectl rollout status deployment/flink-sql-gateway -n flink --timeout=300s
echo ">>> 当前 Pod 镜像（应为 apache/flink:2.0.1-java11）："
kubectl get pods -n flink -o custom-columns=NAME:.metadata.name,IMAGE:.spec.containers[0].image
echo ">>> 完成。Session JM Web/REST：K8s 默认同 http://localhost:8081（LoadBalancer，见 k8s/legacy/flink.yaml）；"
echo ">>>       若用 docker-compose 起本地 JM，默认亦为宿主 8081。SQL Gateway：集群内 8083；宿主见 Ingress 或 NodePort 32483。"
