

# GIDO 统一 Flink 运行时（gido-flink-sql-runner / gido-flink-runtime）
# Flink 2.0.1 + sql-runner.jar + Paimon + Flink CDC SQL connectors
#
# 平台由构建脚本自动检测（集群节点 > 本机 CPU），见 k8s/build-flink-runtime.sh
# 勿裸 docker build；须 buildx --platform 与 TARGETPLATFORM 一致

ARG TARGETPLATFORM
ARG FLINK_BASE_IMAGE=apache/flink:2.0.1-java11
ARG MAVEN_IMAGE=maven:3.9-eclipse-temurin-11

FROM --platform=$TARGETPLATFORM ${MAVEN_IMAGE} AS connectors
WORKDIR /conn
COPY connectors-pom.xml pom.xml
COPY settings.xml settings.xml
# copy-dependencies + 显式拉取 hadoop-common（Paimon catalog 须主 classpath 有 Configuration）
RUN mvn -B -q package -DskipTests -s settings.xml \
    && mvn -B -q dependency:copy \
        -Dartifact=org.apache.hadoop:hadoop-common:3.3.4 \
        -DoutputDirectory=target/connectors \
        -s settings.xml \
    && test -n "$(ls target/connectors/hadoop-common-*.jar)"

FROM --platform=$TARGETPLATFORM ${MAVEN_IMAGE} AS builder
WORKDIR /build
COPY pom.xml settings.xml ./
COPY src ./src
RUN mvn -B -q package -DskipTests -s settings.xml

FROM --platform=$TARGETPLATFORM ${FLINK_BASE_IMAGE}
USER root
RUN mkdir -p /opt/flink/lib /opt/flink/usrlib /opt/flink/gido-scripts /opt/flink/plugins/s3-fs-hadoop \
    && chown -R flink:flink /opt/flink/lib /opt/flink/usrlib /opt/flink/gido-scripts /opt/flink/plugins
COPY --from=connectors /conn/target/connectors/ /tmp/connectors/
RUN mv /tmp/connectors/flink-s3-fs-hadoop-*.jar /opt/flink/plugins/s3-fs-hadoop/ \
    && mv /tmp/connectors/*.jar /opt/flink/lib/ \
    && test -n "$(ls /opt/flink/lib/hadoop-common-*.jar)" \
    && rm -rf /tmp/connectors
COPY --from=builder /build/target/flink-sql-runner-1.0.0.jar /opt/flink/usrlib/sql-runner.jar
USER flink
