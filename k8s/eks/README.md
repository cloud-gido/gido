# GIDO on AWS EKS — CDC→Paimon 补充清单

本目录为 **EKS 生产** 的示例 YAML（占位符须替换后 apply）。完整步骤见 [docs/CDC_PAIMON_EKS.md](../../docs/CDC_PAIMON_EKS.md)。

| 文件 | 用途 |
|------|------|
| `flink-s3-irsa.example.yaml` | Flink 作业 SA + IRSA 注解（S3 warehouse / checkpoint / 读制品） |
| `gido-backend-s3-irsa.example.yaml` | GIDO Backend SA + IRSA（上传 JAR/SQL 制品到 S3） |
| `mysql-cdc-secret.example.yaml` | MySQL CDC 账号 Secret（可选，供 podTemplate env 引用） |
| `gido-backend-eks-overrides.example.yaml` | Backend ConfigMap 补丁：S3 warehouse、checkpoint、制品前缀 |

**应用顺序（摘要）**

1. 创建 S3 bucket、RDS MySQL（binlog）、EKS 集群（`--with-oidc`）
2. 创建 IAM Role + IRSA（`flink-s3-irsa.example.yaml` + `gido-backend-s3-irsa.example.yaml`）
3. 安装 Flink Kubernetes Operator 1.15 + `kubectl apply -f k8s/flink-operator-rbac.yaml`
4. 构建并 push `gido-flink-runtime` 到 ECR（含 S3 插件，见 `k8s/build-flink-runtime.sh`）
5. 部署 GIDO 栈并合并 `gido-backend-eks-overrides.example.yaml` 中的 ConfigMap 项
6. Stream Studio → 插入 CDC→Paimon 模板 → 提交 SQL 作业
