# 部署文档（已合并）

本文件不再维护长文指南。请以以下文档为准：

| 场景 | 文档 |
|------|------|
| 本地全栈（PG + Kafka + Flink Session + Dolphin + GIDO） | 根目录 [README.md](README.md)、`./start-platform.sh` |
| 仅 GIDO（Compose + 外置 PostgreSQL） | [gido/docs/DEPLOYMENT_SOP.md](gido/docs/DEPLOYMENT_SOP.md) |
| AWS EKS | [gido/docs/EKS-DEPLOYMENT-SOP.md](gido/docs/EKS-DEPLOYMENT-SOP.md) |
| **K8s 最小栈**（Kind 本机 / 局域网 K3s / Operator JAR） | **[k8s/README.md](k8s/README.md)**、`k8s/apply-gido-stack.sh`、`k8s/gido.yaml` |
| Flink Session（可选，SQL Gateway） | `k8s/flink.yaml`（默认不部署） |
| Flink Operator RBAC | `k8s/flink-operator-rbac.yaml` |
| 环境变量模板 | 根目录 `.env.example` |
| Kind 本机开发覆盖 | `gido/config/flink-operator.kind-local.env.example` |
| 生产 Operator 配置 | `gido/config/flink-operator.production.env.example` |

**元数据库**：默认 **PostgreSQL**（`GIDO_DATABASE_URL` 或 `INFRA_GIDO_DB_*`）。MySQL 仅代码层可选回退，新环境请勿使用。

**Flink JAR 生产**：**Flink Kubernetes Operator 1.15** + **Flink 2.0.1**（`FlinkDeployment`，`flinkVersion: v2_0`）。K8s 最小栈不含 Session Flink / Dolphin；SQL 需另部署 `k8s/flink.yaml` 或 Compose 全栈。
