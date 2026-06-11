#!/usr/bin/env bash
# 校验 gido-flink-runtime 镜像：Paimon S3 依赖齐全且无冲突 jar
# 用法：bash k8s/flink-sql-runner/verify-image.sh ghcr.io/cloud-gido/gido/gido-flink-runtime:dev
set -euo pipefail

IMAGE="${1:?用法: verify-image.sh <镜像名:tag>}"

echo "==> 校验镜像 ${IMAGE}"

docker run --rm "${IMAGE}" sh -c '
set -e
JAVA_HOME="${JAVA_HOME:-/opt/java/openjdk}"
export PATH="${JAVA_HOME}/bin:${PATH}"

for j in \
  /opt/flink/lib/paimon-flink-2.0-*.jar \
  /opt/flink/lib/hadoop-common-*.jar \
  /opt/flink/lib/hadoop-hdfs-client-*.jar \
  /opt/flink/lib/hadoop-auth-*.jar \
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
for bad in /opt/flink/lib/paimon-s3*.jar; do
  if test -e "$bad"; then
    echo "禁止 paimon-s3（与 flink-s3-fs-hadoop 冲突）: $bad"
    exit 1
  fi
done

CP="$(ls /opt/flink/lib/hadoop-common-*.jar /opt/flink/lib/hadoop-hdfs-client-*.jar \
  /opt/flink/lib/hadoop-auth-*.jar /opt/flink/lib/woodstox-core-*.jar \
  /opt/flink/lib/stax2-api-*.jar | paste -sd: -)"

# Configuration 无 main；用 jshell 实例化（与 Paimon catalog 类加载路径一致）
if command -v jshell >/dev/null 2>&1; then
  printf "%s\n" "new org.apache.hadoop.conf.Configuration();" "/exit" \
    | jshell --class-path "${CP}" -q >/dev/null 2>&1 \
    || { echo "hadoop Configuration 类加载失败 (jshell)"; exit 1; }
else
  java -cp "${CP}" org.apache.hadoop.util.VersionInfo >/dev/null 2>&1 \
    || { echo "hadoop 类加载失败 (VersionInfo)"; exit 1; }
fi
echo "OK Hadoop Configuration 可加载"
'

echo "==> 镜像校验通过: ${IMAGE}"
