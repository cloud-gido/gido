#!/usr/bin/env bash
# 在 K3s / 局域网集群部署 DolphinScheduler 3.2.2（最小分布式）
#
#   export KUBECONFIG=~/.kube/config-mac-orbstack
#   bash k8s/deploy-dolphinscheduler-k3s.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config-mac-orbstack}"
KUBECTL="${KUBECTL:-kubectl}"

echo "==> DolphinScheduler @ K3s"
echo "    KUBECONFIG=${KUBECONFIG}"

${KUBECTL} get nodes -o wide

echo "==> apply ${ROOT}/k8s/legacy/dolphinscheduler.yaml"
${KUBECTL} apply -f "${ROOT}/k8s/legacy/dolphinscheduler.yaml"

echo "==> apply NodePort（局域网 31245）"
${KUBECTL} apply -f "${ROOT}/k8s/legacy/dolphinscheduler-nodeport.yaml"

echo "==> 等待核心 Pod 就绪（首次拉镜像 + 建表约 3～8 分钟）"
components=(postgres zookeeper dolphinscheduler-api dolphinscheduler-master dolphinscheduler-worker dolphinscheduler-alert)
for c in "${components[@]}"; do
  echo "    rollout: ${c}"
  ${KUBECTL} -n dolphinscheduler rollout status "deployment/${c}" --timeout=600s
done

NODE_IP="$(${KUBECTL} get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)"
API_SERVER="$(${KUBECTL} config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null || true)"
LAN_HOST="${NODE_IP:-<节点IP>}"

echo ""
echo "完成。"
${KUBECTL} -n dolphinscheduler get pods -o wide
echo ""
echo "局域网 UI:  http://${LAN_HOST}:31245/dolphinscheduler/ui"
echo "本机也可:   kubectl -n dolphinscheduler port-forward svc/dolphinscheduler-api 12345:12345"
echo "            → http://127.0.0.1:12345/dolphinscheduler/ui"
echo "默认账号:   admin / dolphinscheduler123"
echo ""
echo "GIDO 集群内 DS_URL:"
echo "  http://dolphinscheduler-api.dolphinscheduler.svc.cluster.local:12345/dolphinscheduler"
if [[ -n "${API_SERVER}" ]]; then
  echo "（K8s API: ${API_SERVER}）"
fi
