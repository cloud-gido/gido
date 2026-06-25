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

## Maven 依赖源

Docker 构建默认使用 `settings.xml`（**Maven Central**），供 GitHub Actions 等海外 CI 稳定拉取 Paimon/Flink 依赖。

国内网络较慢时，构建前可临时替换为阿里云镜像：

```bash
cp settings.aliyun.xml settings.xml
bash k8s/build-flink-runtime.sh
git checkout -- settings.xml   # 恢复 Central，避免 CI 502
```

## Maven 依赖

Docker 构建默认使用 `settings.xml`（**Maven Central**），供 GitHub Actions 等海外 CI 稳定拉取 Paimon/Flink 依赖。

国内网络较慢或需镜像时，构建前将 `settings.aliyun.xml` 复制为 `settings.xml`，或：

```bash
cp settings.aliyun.xml settings.xml
bash ../../build-flink-runtime.sh
```

> Maven 对同一 `mirrorOf` 只认第一个 mirror，**不会**在 502 时自动切换备用源。
