#!/usr/bin/env bash
# 校验 gido-flink-runtime 镜像：Paimon S3 依赖齐全且无冲突 jar
# 用法：bash k8s/flink-sql-runner/verify-image.sh ghcr.io/cloud-gido/gido/gido-flink-runtime:dev
set -euo pipefail

IMAGE="${1:?用法: verify-image.sh <镜像名:tag>}"

echo "==> 校验镜像 ${IMAGE} (verify-image v7)"

docker run --rm "${IMAGE}" bash -c '
set -euo pipefail
JAVA="${JAVA_HOME:-/opt/java/openjdk}/bin/java"
test -x "${JAVA}" || JAVA="$(command -v java)"

for j in \
  /opt/flink/lib/paimon-flink-2.0-*.jar \
  /opt/flink/lib/hadoop-common-*.jar \
  /opt/flink/lib/hadoop-hdfs-client-*.jar \
  /opt/flink/lib/hadoop-mapreduce-client-core-*.jar \
  /opt/flink/lib/hadoop-auth-*.jar \
  /opt/flink/lib/commons-configuration2-*.jar \
  /opt/flink/lib/hadoop-shaded-guava-*.jar \
  /opt/flink/lib/woodstox-core-*.jar \
  /opt/flink/lib/stax2-api-*.jar \
  /opt/flink/usrlib/sql-runner.jar \
  /opt/flink/plugins/s3-fs-hadoop/flink-s3-fs-hadoop-*.jar; do
  test -e "$j" || { echo "缺少: $j"; exit 1; }
  echo "OK $j"
done

for bad in commons-cli-1.2.jar log4j-1.2.17.jar; do
  if test -f "/opt/flink/lib/${bad}"; then
    echo "禁止存在: /opt/flink/lib/${bad}"
    exit 1
  fi
done
shopt -s nullglob
bad_jars=(/opt/flink/lib/paimon-s3*.jar)
if ((${#bad_jars[@]})); then
  echo "禁止 paimon-s3（与 flink-s3-fs-hadoop 冲突）: ${bad_jars[*]}"
  exit 1
fi

CP="$(ls /opt/flink/lib/*.jar | paste -sd: -):/opt/flink/usrlib/sql-runner.jar"

# Flink 镜像为 JRE，无 jshell；用 RuntimeSmoke.main 与 SqlRunner 同 classpath
if ! "${JAVA}" -cp "${CP}" com.gido.flink.RuntimeSmoke >/dev/null 2>&1; then
  echo "RuntimeSmoke 失败（Configuration / CatalogContext 初始化）"
  "${JAVA}" -cp "${CP}" com.gido.flink.RuntimeSmoke || true
  exit 1
fi
echo "OK RuntimeSmoke: CatalogContext + ParquetReadOptions"
'

echo "==> 镜像校验通过: ${IMAGE}"
