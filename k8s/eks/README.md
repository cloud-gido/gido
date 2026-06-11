# GIDO on AWS EKS — CDC→Paimon 补充清单

本目录为 **EKS 生产** 的示例 YAML（占位符须替换后 apply）。完整步骤见 [docs/CDC_PAIMON_EKS.md](../../docs/CDC_PAIMON_EKS.md)。

| 文件 | 用途 |
|------|------|
| **`gido-eks-external-pg.yaml`** | **一键清单**：外置 RDS PG + backend/frontend + S3 配置 + ALB Ingress |
| `apply-gido-eks.sh` | 替换 `CHANGE_ME_*` 占位符并 `kubectl apply` |
| `flink-s3-irsa.example.yaml` | Flink 作业 SA + IRSA 注解（S3 warehouse / checkpoint / 读制品） |
| `gido-backend-s3-irsa.example.yaml` | GIDO Backend SA + IRSA（上传 JAR/SQL 制品到 S3） |
| `mysql-cdc-secret.example.yaml` | MySQL CDC 账号 Secret（可选，供 podTemplate env 引用） |
| `gido-backend-eks-overrides.example.yaml` | 仅 ConfigMap 补丁（已有 gido.yaml 时 merge） |

**应用顺序（摘要）**

1. 创建 S3 bucket、RDS MySQL（binlog）、EKS 集群（`--with-oidc`）
2. 创建 IAM Role + IRSA（`flink-s3-irsa.example.yaml` + `gido-backend-s3-irsa.example.yaml`）
3. 安装 Flink Kubernetes Operator 1.15 + `kubectl apply -f k8s/flink-operator-rbac.yaml`
4. Flink 运行时镜像：GitHub `dev`/`main` push 后 CI 自动推 GHCR（**一次构建、两包同名 digest**）：
   - `ghcr.io/cloud-gido/gido/gido-flink-runtime` ← **EKS 配置用这个**
   - `ghcr.io/cloud-gido/gido/gido-flink-sql-runner` ← 与上行完全相同
   - 见 `k8s/flink-sql-runner/README.md`、`.github/workflows/ci.yml`
   - 自建 ECR 时：`k8s/build-flink-runtime.sh`
5. 部署 GIDO：**外置 RDS** 用 `gido-eks-external-pg.yaml`（或 `bash k8s/eks/apply-gido-eks.sh`）；已有集群内栈则 merge `gido-backend-eks-overrides.example.yaml`
6. ConfigMap 配置 `FLINK_OPERATOR_NODE_POOL=bigdata`（多节点池 + taint 集群必填）
7. Stream Studio → 插入 CDC→Paimon 模板 → 提交 SQL 作业
