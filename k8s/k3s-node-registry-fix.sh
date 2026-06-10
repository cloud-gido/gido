#!/usr/bin/env bash

# 在 Mac 上运行：打印需在 OrbStack ubuntu K3s 节点执行的 registry 修复命令
#
# 根因常见两类：
# 1) 节点宿主机无法解析 registry.gido.svc.cluster.local → HTTP mirror 失败 → 回退 HTTPS → EOF
# 2) containerd 默认 HTTPS endpoint 回退 → 需 disable-default-registry-endpoint
#
# 用法：
#   export KUBECONFIG=~/.kube/config-mac-orbstack
#   bash k8s/k3s-node-registry-fix.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"

REG_IP="$(${KUBECTL} -n gido get svc registry -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)"
if [[ -z "${REG_IP}" ]]; then
  echo "错误：未找到 gido/registry Service，请先 apply k8s/registry.yaml" >&2
  exit 1
fi

echo "==> apply HelmChartConfig（mirrors）"
${KUBECTL} apply -f "${ROOT}/k8s/k3s-insecure-registry.yaml"

cat <<EOF

================================================================================
请在 OrbStack ubuntu 节点（用户 infras）上执行以下命令：
================================================================================

# 1) 节点解析 registry 集群 DNS（宿主机常解析不了 *.svc.cluster.local）
REG_IP="${REG_IP}"
if ! grep -q 'registry.gido.svc.cluster.local' /etc/hosts; then
  echo "\${REG_IP} registry.gido.svc.cluster.local" | sudo tee -a /etc/hosts
else
  echo "hosts 已有 registry.gido.svc.cluster.local，请确认指向 \${REG_IP}"
  grep registry.gido.svc.cluster.local /etc/hosts
fi

# 2) 禁止 containerd 对自定义 registry 回退到 HTTPS 默认 endpoint
sudo mkdir -p /etc/rancher/k3s
if [[ -f /etc/rancher/k3s/config.yaml ]]; then
  grep -q 'disable-default-registry-endpoint' /etc/rancher/k3s/config.yaml || \\
    echo 'disable-default-registry-endpoint: true' | sudo tee -a /etc/rancher/k3s/config.yaml
else
  echo 'disable-default-registry-endpoint: true' | sudo tee /etc/rancher/k3s/config.yaml
fi

# 3) 确认 registries.yaml（键名请带引号，与下面一致）
sudo tee /etc/rancher/k3s/registries.yaml >/dev/null <<'YAML'
mirrors:
  docker.io:
    endpoint:
      - "https://docker.1ms.run"
  registry-1.docker.io:
    endpoint:
      - "https://docker.1ms.run"
  "registry.gido.svc.cluster.local:5000":
    endpoint:
      - "http://registry.gido.svc.cluster.local:5000"
  "${REG_IP}:5000":
    endpoint:
      - "http://${REG_IP}:5000"
configs:
  "registry.gido.svc.cluster.local:5000":
    tls:
      insecure_skip_verify: true
  "${REG_IP}:5000":
    tls:
      insecure_skip_verify: true
YAML

# 4) 重启并试拉（必须 sudo）
sudo systemctl restart k3s
sleep 5
curl -sf http://\${REG_IP}:5000/v2/_catalog
sudo k3s crictl pull registry.gido.svc.cluster.local:5000/gido-backend:orbstack
sudo k3s crictl pull registry.gido.svc.cluster.local:5000/gido-frontend:orbstack

================================================================================
节点试拉成功后，在 Mac 上执行：
  kubectl -n gido rollout restart deployment/gido-backend deployment/gido-frontend
  kubectl -n gido rollout status deployment/gido-backend --timeout=300s
  kubectl -n gido get pods -n gido
================================================================================
EOF
