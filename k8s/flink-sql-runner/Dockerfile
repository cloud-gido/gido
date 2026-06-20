

# GIDO 统一 Flink 运行时（gido-flink-runtime）
# 版本与 connector 坐标由 k8s/flink-runtime/<flink_version>/version.json 定义；
# 构建前须 render：python3 k8s/flink-runtime/scripts/render_build_context.py <flink_version>
#
# 平台由构建脚本自动检测，见 k8s/build-flink-runtime.sh
# 构建后校验：bash k8s/flink-runtime/scripts/verify-image.sh <tag> [runtime_key]

ARG TARGETPLATFORM
ARG RUNTIME_PROFILE=2.2.1
ARG FLINK_BASE_IMAGE=apache/flink:2.2.1-java11
ARG SQL_RUNNER_ARTIFACT_VERSION=1.0.0
ARG MAVEN_IMAGE=maven:3.9-eclipse-temurin-11
ARG MAVEN_SETTINGS=settings.xml

FROM --platform=$TARGETPLATFORM ${MAVEN_IMAGE} AS connectors
ARG RUNTIME_PROFILE
ARG MAVEN_SETTINGS=settings.xml
WORKDIR /conn
COPY .build/${RUNTIME_PROFILE}/connectors-pom.xml pom.xml
COPY settings.xml settings.aliyun.xml settings.ci.xml ./
RUN cp "${MAVEN_SETTINGS:-settings.xml}" settings-active.xml
COPY .build/${RUNTIME_PROFILE}/hadoop-libs.txt hadoop-libs.txt
COPY .build/${RUNTIME_PROFILE}/security-overrides.txt security-overrides.txt
COPY dedupe-lib-jars.sh dedupe-lib-jars.sh
COPY .build/${RUNTIME_PROFILE}/verify-connectors.sh verify-connectors.sh
RUN mvn -B -q package -DskipTests -s settings-active.xml -gs settings-active.xml \
    && grep -v '^#' hadoop-libs.txt | grep -v '^[[:space:]]*$' | while IFS= read -r artifact; do \
        mvn -B -q -o org.apache.maven.plugins:maven-dependency-plugin:3.7.1:copy -Dartifact="${artifact}" \
            -DoutputDirectory=target/connectors -s settings-active.xml -gs settings-active.xml; \
    done \
    && grep -v '^#' security-overrides.txt | grep -v '^[[:space:]]*$' | while IFS= read -r artifact; do \
        mvn -B -q -o org.apache.maven.plugins:maven-dependency-plugin:3.7.1:copy -Dartifact="${artifact}" \
            -DoutputDirectory=target/connectors -s settings-active.xml -gs settings-active.xml; \
    done \
    && chmod +x dedupe-lib-jars.sh verify-connectors.sh \
    && ./dedupe-lib-jars.sh target/connectors security-overrides.txt \
    && ./verify-connectors.sh

FROM --platform=$TARGETPLATFORM ${MAVEN_IMAGE} AS builder
ARG RUNTIME_PROFILE
ARG MAVEN_SETTINGS=settings.xml
WORKDIR /build
COPY .build/${RUNTIME_PROFILE}/sql-runner-pom.xml pom.xml
COPY settings.xml settings.aliyun.xml settings.ci.xml ./
RUN cp "${MAVEN_SETTINGS:-settings.xml}" settings-active.xml
COPY src ./src
RUN mvn -B -q package -DskipTests -s settings-active.xml -gs settings-active.xml

FROM --platform=$TARGETPLATFORM ${FLINK_BASE_IMAGE}
ARG RUNTIME_PROFILE
ARG SQL_RUNNER_ARTIFACT_VERSION
USER root
RUN mkdir -p /opt/flink/lib /opt/flink/usrlib /opt/flink/gido-scripts /opt/flink/plugins/s3-fs-hadoop \
    && chown -R flink:flink /opt/flink/lib /opt/flink/usrlib /opt/flink/gido-scripts /opt/flink/plugins
COPY --from=connectors /conn/target/connectors/ /tmp/connectors/
COPY .build/${RUNTIME_PROFILE}/verify-layout.sh /tmp/verify-layout.sh
RUN bash -ec '\
    shopt -s nullglob; \
    s3=(/tmp/connectors/flink-s3-fs-hadoop-*.jar); \
    if ((${#s3[@]} == 0)); then echo "缺少 flink-s3-fs-hadoop plugin jar"; ls -la /tmp/connectors/ || true; exit 1; fi; \
    mv "${s3[@]}" /opt/flink/plugins/s3-fs-hadoop/; \
    lib=(/tmp/connectors/*.jar); \
    if ((${#lib[@]})); then mv "${lib[@]}" /opt/flink/lib/; fi; \
    rm -rf /tmp/connectors \
    '
COPY --from=builder /build/target/flink-sql-runner-${SQL_RUNNER_ARTIFACT_VERSION}.jar /opt/flink/usrlib/sql-runner.jar
RUN chmod +x /tmp/verify-layout.sh \
    && /tmp/verify-layout.sh \
    && rm -f /tmp/verify-layout.sh
USER flink
