# GIDO Flink 统一运行时镜像

各 Flink 基座版本配置位于 **`k8s/flink-runtime/<flink_version>/`**（目录名 = `version.json` 中的 `flink_version`）；索引见 `runtime-versions.json`。构建前自动 render 到 `.build/<flink_version>/`。架构说明见 [`../flink-runtime/ARCHITECTURE.md`](../flink-runtime/ARCHITECTURE.md)。

CI 按 `ci_matrix` 并行构建 **`gido-flink-runtime`** 并推 GHCR。本地 `k8s/build-flink-runtime.sh` 打 `gido-flink-sql-runner:<tag>` 并 alias 为 `gido-flink-runtime:<tag>`。

## 构建

```bash
# 默认版本（runtime-versions.json → default，当前 2.2.1）
bash k8s/build-flink-runtime.sh

# 指定 Flink 基座版本
GIDO_FLINK_RUNTIME_VERSION=1.17.2 bash k8s/build-flink-runtime.sh
GIDO_FLINK_RUNTIME_VERSION=2.0.1 bash k8s/build-flink-runtime.sh

# 渲染 POM / verify 脚本
python3 k8s/flink-runtime/scripts/render_build_context.py --list
python3 k8s/flink-runtime/scripts/render_build_context.py --all
python3 k8s/flink-runtime/scripts/render_build_context.py 2.2.1
```

## 校验

```bash
bash k8s/flink-runtime/scripts/verify-image.sh gido-flink-runtime:orbstack
bash k8s/flink-runtime/scripts/verify-image.sh gido-flink-runtime:orbstack 2.2.1
```

## 镜像内容（默认 2.2.1）

- 基座：`apache/flink:2.2.1-java11`
- `/opt/flink/usrlib/sql-runner.jar`
- Paimon、MySQL/Postgres CDC、Hadoop 白名单 → `/opt/flink/lib/`
- S3 插件 → `/opt/flink/plugins/s3-fs-hadoop/`

Hadoop / CVE 清单见 `k8s/flink-runtime/<flink_version>/hadoop-libs.txt` 与 `security-overrides.txt`。

Maven 默认 **Maven Central**（`settings.xml`）。国内：

```bash
python3 k8s/flink-runtime/scripts/render_build_context.py --default
docker build \
  --build-arg MAVEN_SETTINGS=settings.aliyun.xml \
  --build-arg RUNTIME_PROFILE=2.2.1 \
  -f k8s/flink-sql-runner/Dockerfile k8s/flink-sql-runner
```

## 遗留文件

- `pom.xml`、`connectors-pom.xml`：IDE 参考；Docker 构建使用 `.build/<flink_version>/` 下 render 结果。
- `hadoop-libs.txt`、`security-overrides.txt`（本目录）：说明性副本；权威配置在 `k8s/flink-runtime/<flink_version>/`。
