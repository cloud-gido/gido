# GIDO Flink 统一运行时镜像

单一 Dockerfile，CI 仅推送 **`gido-flink-runtime`** 至 GHCR（`.github/workflows/ci.yml` → `docker-flink-runtime`）。

本地 `k8s/build-flink-runtime.sh` 仍会打 `gido-flink-sql-runner:<tag>` 标签并 `docker tag` 为 `gido-flink-runtime:<tag>` 别名，便于与历史脚本兼容。

## 镜像内容

- 基座：`apache/flink:2.0.1-java11`
- `/opt/flink/usrlib/sql-runner.jar`（GIDO SQL 入口，`FLINK_OPERATOR_SQL_RUNNER_JAR_URI`）
- Paimon、MySQL/Postgres CDC、hadoop-common/hdfs-client/auth → `/opt/flink/lib/`
- S3 插件 → `/opt/flink/plugins/s3-fs-hadoop/`

构建后自检：

```bash
docker run --rm gido-flink-runtime:<tag> sh -c '
  ls /opt/flink/lib/hadoop-common-*.jar
  ls /opt/flink/lib/hadoop-hdfs-client-*.jar
  test ! -f /opt/flink/lib/commons-cli-1.2.jar && echo commons-cli OK
'
```

## K8s 配置

```yaml
FLINK_OPERATOR_IMAGE: "ghcr.io/cloud-gido/gido/gido-flink-runtime:2.0.1"
FLINK_K8S_APPLICATION_IMAGE: "ghcr.io/cloud-gido/gido/gido-flink-runtime:2.0.1"
FLINK_OPERATOR_SQL_RUNNER_JAR_URI: "local:///opt/flink/usrlib/sql-runner.jar"
```

## 本地构建

```bash
bash k8s/build-flink-runtime.sh
# 本地 tag：gido-flink-sql-runner:<tag>，可 docker tag 为 gido-flink-runtime:<tag>
```
