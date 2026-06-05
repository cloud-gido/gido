# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
根据 FlinkRuntimeConfig 生成「集群内 SQL Gateway」Deployment YAML 片段，
与仓库 k8s/flink.yaml 中 flink-sql-gateway 逻辑对齐，便于对接不同 K8s（集群域、命名空间、apiserver 回退等）。
"""
from __future__ import annotations

import shlex
from string import Template

from app.services.flink_runtime import FlinkRuntimeConfig, resolved_flink_k8s_apiserver_fallback, resolved_flink_k8s_jm_rpc_host, resolved_flink_k8s_namespace, resolved_flink_k8s_sql_gateway_rest_host


def render_flink_sql_gateway_deployment_yaml(cfg: FlinkRuntimeConfig) -> str:
    ns = resolved_flink_k8s_namespace(cfg)
    jm = resolved_flink_k8s_jm_rpc_host(cfg)
    gw = resolved_flink_k8s_sql_gateway_rest_host(cfg)
    fb = resolved_flink_k8s_apiserver_fallback(cfg)
    fb_q = shlex.quote(fb)
    img = (cfg.flink_k8s_application_image or "apache/flink:2.0.1-java11").strip()

    # 与 k8s/flink.yaml 中 init + sql-gateway 容器等价；apiserver 回退 URL 经 shlex 安全嵌入 shell。
    t = Template(
        """# 由 GIDO「系统管理 → 集成」当前生效配置生成；可 kubectl apply -f -
# 须已存在 ServiceAccount flink-sql-gateway 与 Role flink-sql-gateway-application（见仓库 k8s/flink.yaml 全文）。
apiVersion: apps/v1
kind: Deployment
metadata:
  name: flink-sql-gateway
  namespace: $ns
spec:
  replicas: 1
  selector:
    matchLabels:
      app: flink
      component: sql-gateway
  template:
    metadata:
      labels:
        app: flink
        component: sql-gateway
    spec:
      serviceAccountName: flink-sql-gateway
      enableServiceLinks: true
      volumes:
        - name: sql-gateway-kubeconfig
          emptyDir: {}
      initContainers:
        - name: write-incluster-kubeconfig
          image: $img
          imagePullPolicy: IfNotPresent
          command: ["/bin/bash", "-exc"]
          args:
            - |
              set -e
              if [[ -n "$${KUBERNETES_SERVICE_HOST:-}" && -n "$${KUBERNETES_SERVICE_PORT:-}" ]]; then
                SERVER="https://$${KUBERNETES_SERVICE_HOST}:$${KUBERNETES_SERVICE_PORT}"
              else
                SERVER=$fb_q
              fi
              mkdir -p /kube
              {
                echo "apiVersion: v1"
                echo "kind: Config"
                echo "clusters:"
                echo "- name: incluster"
                echo "  cluster:"
                echo "    certificate-authority: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
                echo "    server: $${SERVER}"
                echo "users:"
                echo "- name: sa"
                echo "  user:"
                echo "    tokenFile: /var/run/secrets/kubernetes.io/serviceaccount/token"
                echo "contexts:"
                echo "- name: ctx"
                echo "  context:"
                echo "    cluster: incluster"
                echo "    user: sa"
                echo "    namespace: $ns"
                echo "current-context: ctx"
              } > /kube/config
          volumeMounts:
            - name: sql-gateway-kubeconfig
              mountPath: /kube
      containers:
        - name: sql-gateway
          image: $img
          imagePullPolicy: Always
          env:
            - name: KUBECONFIG
              value: /opt/flink/.kube/config
          command: ["/bin/bash", "-exc"]
          args:
            - |
              set -e
              mkdir -p /opt/flink/.kube
              cp -f /kube/config /opt/flink/.kube/config
              exec /opt/flink/bin/sql-gateway.sh start-foreground \\
                -Dkubernetes.config.file=/opt/flink/.kube/config \\
                -Dkubernetes.namespace=$ns \\
                -Djobmanager.rpc.address=$jm \\
                -Djobmanager.rpc.port=6123 \\
                -Dsql-gateway.endpoint.rest.address=$gw \\
                -Dsql-gateway.endpoint.rest.bind-address=0.0.0.0 \\
                -Dsql-gateway.endpoint.rest.port=8083
          volumeMounts:
            - name: sql-gateway-kubeconfig
              mountPath: /kube
              readOnly: true
          ports:
            - name: rest
              containerPort: 8083
"""
    )
    return t.substitute(ns=ns, jm=jm, gw=gw, fb_q=fb_q, img=img)
