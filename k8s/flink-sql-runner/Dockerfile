

# GIDO 统一 Flink 运行时（gido-flink-runtime）
# Flink 2.2.1 + sql-runner.jar + Paimon + CDC + S3 插件 + Hadoop 白名单（Paimon catalog）
#
# 平台由构建脚本自动检测，见 k8s/build-flink-runtime.sh
# 构建后校验：bash k8s/flink-sql-runner/verify-image.sh <tag>

ARG TARGETPLATFORM
ARG FLINK_BASE_IMAGE=apache/flink:2.2.1-java11
ARG MAVEN_IMAGE=maven:3.9-eclipse-temurin-11
ARG MAVEN_SETTINGS=settings.xml

FROM --platform=$TARGETPLATFORM ${MAVEN_IMAGE} AS connectors
WORKDIR /conn
COPY connectors-pom.xml pom.xml
COPY ${MAVEN_SETTINGS} settings.xml
COPY hadoop-libs.txt hadoop-libs.txt
COPY security-overrides.txt security-overrides.txt
COPY dedupe-lib-jars.sh dedupe-lib-jars.sh
RUN mvn -B -q package -DskipTests -s settings.xml -gs settings.xml \
    && grep -v '^#' hadoop-libs.txt | grep -v '^[[:space:]]*$' | while IFS= read -r artifact; do \
        mvn -B -q dependency:copy -Dartifact="${artifact}" \
            -DoutputDirectory=target/connectors -s settings.xml -gs settings.xml; \
    done \
    && grep -v '^#' security-overrides.txt | grep -v '^[[:space:]]*$' | while IFS= read -r artifact; do \
        mvn -B -q dependency:copy -Dartifact="${artifact}" \
            -DoutputDirectory=target/connectors -s settings.xml -gs settings.xml; \
    done \
    && chmod +x dedupe-lib-jars.sh \
    && ./dedupe-lib-jars.sh target/connectors security-overrides.txt \
    && test -n "$(ls target/connectors/hadoop-common-*.jar)" \
    && test -n "$(ls target/connectors/hadoop-hdfs-client-*.jar)" \
    && test -n "$(ls target/connectors/hadoop-mapreduce-client-core-*.jar)" \
    && test -n "$(ls target/connectors/commons-configuration2-*.jar)" \
    && test -n "$(ls target/connectors/woodstox-core-*.jar)" \
    && test -n "$(ls target/connectors/netty-codec-4.2.13.Final.jar)" \
    && test -n "$(ls target/connectors/snappy-java-1.1.10.7.jar)"

FROM --platform=$TARGETPLATFORM ${MAVEN_IMAGE} AS builder
ARG MAVEN_SETTINGS=settings.xml
WORKDIR /build
COPY pom.xml ./
COPY ${MAVEN_SETTINGS} settings.xml
COPY src ./src
RUN mvn -B -q package -DskipTests -s settings.xml -gs settings.xml

FROM --platform=$TARGETPLATFORM ${FLINK_BASE_IMAGE}
USER root
RUN mkdir -p /opt/flink/lib /opt/flink/usrlib /opt/flink/gido-scripts /opt/flink/plugins/s3-fs-hadoop \
    && chown -R flink:flink /opt/flink/lib /opt/flink/usrlib /opt/flink/gido-scripts /opt/flink/plugins
COPY --from=connectors /conn/target/connectors/ /tmp/connectors/
RUN mv /tmp/connectors/flink-s3-fs-hadoop-*.jar /opt/flink/plugins/s3-fs-hadoop/ \
    && mv /tmp/connectors/*.jar /opt/flink/lib/ \
    && test -n "$(ls /opt/flink/lib/hadoop-common-*.jar)" \
    && test -n "$(ls /opt/flink/lib/hadoop-hdfs-client-*.jar)" \
    && test -n "$(ls /opt/flink/lib/hadoop-mapreduce-client-core-*.jar)" \
    && test -n "$(ls /opt/flink/lib/commons-configuration2-*.jar)" \
    && test -n "$(ls /opt/flink/lib/woodstox-core-*.jar)" \
    && test -n "$(ls /opt/flink/lib/netty-codec-4.2.13.Final.jar)" \
    && test -n "$(ls /opt/flink/lib/snappy-java-1.1.10.7.jar)" \
    && test -n "$(ls /opt/flink/lib/paimon-flink-2.2-*.jar)" \
    && test -n "$(ls /opt/flink/plugins/s3-fs-hadoop/flink-s3-fs-hadoop-*.jar)" \
    && rm -f /opt/flink/lib/commons-cli-1.2.jar /opt/flink/lib/log4j-1.2.17.jar \
    && rm -rf /tmp/connectors
COPY --from=builder /build/target/flink-sql-runner-1.0.0.jar /opt/flink/usrlib/sql-runner.jar
USER flink
