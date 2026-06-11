# GIDO Flink 统一运行时镜像

单一 Dockerfile，产出 **两个 GHCR 包名、同一 digest**：

| GHCR 包 | 用途 |
|---------|------|
| `ghcr.io/<org>/gido/gido-flink-sql-runner` | 构建脚本主名（`k8s/lib/flink-sql-runner-image.sh`） |
| `ghcr.io/<org>/gido/gido-flink-runtime` | **EKS / Operator 配置用名**（`FLINK_OPERATOR_IMAGE`） |

CI 见 `.github/workflows/ci.yml` → job `docker-flink-runtime`：`docker/metadata-action` 列出两个 `images`，`docker/build-push-action` **只 build 一次**，所有 tag 同时推到两个包，内容字节级相同。

## 镜像内容

- 基座：`apache/flink:2.0.1-java11`
- `/opt/flink/usrlib/sql-runner.jar`（GIDO SQL 入口，`FLINK_OPERATOR_SQL_RUNNER_JAR_URI`）
- Paimon、MySQL/Postgres CDC → `/opt/flink/lib/`
- S3 插件 → `/opt/flink/plugins/s3-fs-hadoop/`

## K8s 配置（二选一，推荐 runtime）

```yaml
FLINK_OPERATOR_IMAGE: "ghcr.io/cloud-gido/gido/gido-flink-runtime:2.0.1"
FLINK_K8S_APPLICATION_IMAGE: "ghcr.io/cloud-gido/gido/gido-flink-runtime:2.0.1"
FLINK_OPERATOR_SQL_RUNNER_JAR_URI: "local:///opt/flink/usrlib/sql-runner.jar"
```

`gido-flink-sql-runner:2.0.1` 与 `gido-flink-runtime:2.0.1` 可互换，**不必在 YAML 里写两个镜像**。

## 本地构建

```bash
bash k8s/build-flink-runtime.sh
# 本地 tag：gido-flink-sql-runner:<tag>，可 docker tag 为 gido-flink-runtime:<tag>
```
