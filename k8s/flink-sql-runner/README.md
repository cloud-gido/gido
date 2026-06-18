# GIDO Flink 统一运行时镜像

版本与 connector 坐标由 **`k8s/flink-runtime/runtime-versions.json`** 统一定义；构建前自动 render 到 `.build/<version>/`。架构说明见 [`../flink-runtime/ARCHITECTURE.md`](../flink-runtime/ARCHITECTURE.md)。

CI 推送 **`gido-flink-runtime`** 至 GHCR（`.github/workflows/ci.yml`）。本地 `k8s/build-flink-runtime.sh` 打 `gido-flink-sql-runner:<tag>` 并 alias 为 `gido-flink-runtime:<tag>`。

## 构建

```bash
# 默认版本（runtime-versions.json → default，当前 2.2.1）
bash k8s/build-flink-runtime.sh

# 指定 Flink 版本 profile
GIDO_FLINK_RUNTIME_VERSION=1.17.2 bash k8s/build-flink-runtime.sh

# 仅渲染 POM / verify 脚本
python3 k8s/flink-runtime/scripts/render_build_context.py --list
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

Hadoop / CVE 清单见 `k8s/flink-runtime/profiles/<version>/`。

Maven 默认 **Maven Central**（`settings.xml`）。国内：

```bash
python3 k8s/flink-runtime/scripts/render_build_context.py --default
docker build \
  --build-arg MAVEN_SETTINGS=settings.aliyun.xml \
  --build-arg RUNTIME_PROFILE=2.2.1 \
  -f k8s/flink-sql-runner/Dockerfile k8s/flink-sql-runner
```

## 遗留文件

- `pom.xml`、`connectors-pom.xml`：IDE 参考；Docker 构建使用 `.build/<version>/` 下 render 结果。
- `hadoop-libs.txt`、`security-overrides.txt`：指向 `profiles/` 的副本说明；勿与 profile 分叉维护。
