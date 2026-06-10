# 部署文档（已合并）

本文件不再维护长文指南。请以以下文档为准：

| 场景 | 文档 |
|------|------|
| 本地全栈（PG + Kafka + Flink Session + Dolphin + GIDO） | 根目录 [README.md](README.md)、`./start-platform.sh` |
| 仅 GIDO（Compose + 外置 PostgreSQL） | [gido/docs/DEPLOYMENT_SOP.md](gido/docs/DEPLOYMENT_SOP.md) |
| AWS EKS | [gido/docs/EKS-DEPLOYMENT-SOP.md](gido/docs/EKS-DEPLOYMENT-SOP.md) · [docs/CDC_PAIMON_EKS.md](docs/CDC_PAIMON_EKS.md) · [k8s/eks/](k8s/eks/) |
| **K3s 分层部署**（应用每次构建 / Flink 运行时按需） | `bash k8s/deploy-gido-k3s.sh` · [k8s/gido-deploy.env.example](k8s/gido-deploy.env.example) |
| **K8s 最小栈**（Kind 本机 / 局域网 K3s / Operator JAR） | **[k8s/README.md](k8s/README.md)**、`k8s/apply-gido-stack.sh`、`k8s/gido.yaml` |
| Flink Session（可选，遗留） | `k8s/legacy/flink.yaml`（默认不部署） |
| Flink Operator RBAC | `k8s/flink-operator-rbac.yaml` |
| 环境变量模板 | 根目录 `.env.example` |
| Kind 本机开发覆盖 | `gido/config/flink-operator.kind-local.env.example` |
| 生产 Operator 配置 | `gido/config/flink-operator.production.env.example` |

**元数据库**：默认 **PostgreSQL**（`GIDO_DATABASE_URL` 或 `INFRA_GIDO_DB_*`）。MySQL 仅代码层可选回退，新环境请勿使用。

**Flink JAR 生产**：**Flink Kubernetes Operator 1.15** + **Flink 2.0.1**（`FlinkDeployment`，`flinkVersion: v2_0`）。K8s 最小栈不含 Session Flink / Dolphin；SQL 走 Operator + `gido-flink-runtime` 镜像，或 Compose 全栈 / 遗留 `k8s/legacy/flink.yaml`。

**S3 制品库（EKS 生产）**：配置 `FLINK_OPERATOR_JAR_S3_PREFIX`（如 `s3://<bucket>/gido-artifacts`），Backend 上传 JAR/SQL，Operator Pod 以 `s3://` 拉取；需 Backend + Flink 双 IRSA，见 [k8s/eks/](k8s/eks/)。

**CDC→Paimon（EKS）**：RDS MySQL binlog + S3 Paimon warehouse + checkpoint；Stream Studio 内置模板，详见 [docs/CDC_PAIMON_EKS.md](docs/CDC_PAIMON_EKS.md)。

**功能完整度**：见 [docs/PRODUCT_MATURITY.md](docs/PRODUCT_MATURITY.md)。
