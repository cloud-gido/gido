# GIDO Flink 统一运行时镜像

单一 Dockerfile，CI 仅推送 **`gido-flink-runtime`** 至 GHCR（`.github/workflows/ci.yml` → `docker-flink-runtime`）。

本地 `k8s/build-flink-runtime.sh` 仍会打 `gido-flink-sql-runner:<tag>` 标签并 `docker tag` 为 `gido-flink-runtime:<tag>` 别名，便于与历史脚本兼容。

## 镜像内容

- 基座：`apache/flink:2.0.1-java11`
- `/opt/flink/usrlib/sql-runner.jar`（GIDO SQL 入口，`FLINK_OPERATOR_SQL_RUNNER_JAR_URI`）
- Paimon、MySQL/Postgres CDC、hadoop-common/hdfs-client/auth、woodstox → `/opt/flink/lib/`
- S3 插件 → `/opt/flink/plugins/s3-fs-hadoop/`

构建后自检：

```bash
bash k8s/flink-sql-runner/verify-image.sh ghcr.io/cloud-gido/gido/gido-flink-runtime:dev
```

本地构建：

```bash
bash k8s/build-flink-runtime.sh
bash k8s/flink-sql-runner/verify-image.sh gido-flink-runtime:orbstack
```

Hadoop 白名单见 `hadoop-libs.txt`（与 `k8s/flink-runtime/hadoop-libs.txt` 同步）。
