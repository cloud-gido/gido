# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""统一 Flink 运行时镜像内预置连接器清单（与 k8s/flink-runtime/connectors.manifest 对齐）。"""

from typing import List

from app.core.config import settings
from app.services.flink_version import (
    display_flink_version_from_runtime,
    infer_operator_flink_version_from_image,
    supported_operator_flink_versions_public,
)

BUNDLED_CONNECTORS: List[dict] = [
    {
        "id": "paimon",
        "name": "Apache Paimon",
        "artifact": "org.apache.paimon:paimon-flink-2.2",
        "version": "1.4.1",
        "path": "/opt/flink/lib/paimon-flink-2.2-1.4.1.jar",
    },
    {
        "id": "mysql-cdc",
        "name": "Flink CDC MySQL",
        "artifact": "org.apache.flink:flink-sql-connector-mysql-cdc",
        "version": "3.6.0-2.2",
        "connector": "mysql-cdc",
    },
    {
        "id": "postgres-cdc",
        "name": "Flink CDC PostgreSQL",
        "artifact": "org.apache.flink:flink-sql-connector-postgres-cdc",
        "version": "3.6.0-2.2",
        "connector": "postgres-cdc",
    },
    {
        "id": "s3-fs-hadoop",
        "name": "Flink S3 Filesystem (Hadoop)",
        "artifact": "org.apache.flink:flink-s3-fs-hadoop",
        "version": "2.2.1",
        "path": "/opt/flink/plugins/s3-fs-hadoop/flink-s3-fs-hadoop-2.2.1.jar",
        "scheme": "s3://",
    },
    {
        "id": "hadoop-common",
        "name": "Hadoop Common (Paimon catalog)",
        "artifact": "org.apache.hadoop:hadoop-common",
        "version": "3.3.4",
        "path": "/opt/flink/lib/hadoop-common-3.3.4.jar",
    },
    {
        "id": "hadoop-hdfs-client",
        "name": "Hadoop HDFS Client (Paimon CatalogContext)",
        "artifact": "org.apache.hadoop:hadoop-hdfs-client",
        "version": "3.3.4",
        "path": "/opt/flink/lib/hadoop-hdfs-client-3.3.4.jar",
    },
    {
        "id": "hadoop-mapreduce-client-core",
        "name": "Hadoop MapReduce Client (Paimon Parquet stats)",
        "artifact": "org.apache.hadoop:hadoop-mapreduce-client-core",
        "version": "3.3.4",
        "path": "/opt/flink/lib/hadoop-mapreduce-client-core-3.3.4.jar",
    },
    {
        "id": "hadoop-auth",
        "name": "Hadoop Auth (Paimon / S3 credentials)",
        "artifact": "org.apache.hadoop:hadoop-auth",
        "version": "3.3.4",
        "path": "/opt/flink/lib/hadoop-auth-3.3.4.jar",
    },
    {
        "id": "commons-configuration2",
        "name": "Commons Configuration2 (Hadoop Configuration init)",
        "artifact": "org.apache.commons:commons-configuration2",
        "version": "2.1.1",
        "path": "/opt/flink/lib/commons-configuration2-2.1.1.jar",
    },
    {
        "id": "hadoop-shaded-guava",
        "name": "Hadoop Shaded Guava",
        "artifact": "org.apache.hadoop.thirdparty:hadoop-shaded-guava",
        "version": "1.1.1",
        "path": "/opt/flink/lib/hadoop-shaded-guava-1.1.1.jar",
    },
    {
        "id": "woodstox-core",
        "name": "Woodstox XML (HdfsConfiguration default XML)",
        "artifact": "com.fasterxml.woodstox:woodstox-core",
        "version": "5.3.0",
        "path": "/opt/flink/lib/woodstox-core-5.3.0.jar",
    },
    {
        "id": "stax2-api",
        "name": "StAX2 API (Woodstox)",
        "artifact": "org.codehaus.woodstox:stax2-api",
        "version": "4.2.1",
        "path": "/opt/flink/lib/stax2-api-4.2.1.jar",
    },
]

CDC_FLINK_COMPATIBILITY_NOTE = (
    "Flink CDC 3.6+ 在 Maven 为 3.6.0-1.20 / 3.6.0-2.2（无裸 3.6.0）。"
    "平台默认 gido-flink-runtime 2.2.1 预置 3.6.0-2.2；"
    "若使用 Flink 1.17.2 等其它运行时镜像，CDC/Paimon 以该镜像内实际 connector 为准。"
)

SQL_RUNNER_INFO = {
    "path": "/opt/flink/usrlib/sql-runner.jar",
    "entry_class": "com.gido.flink.SqlRunner",
    "artifact": "com.gido:flink-sql-runner:1.0.0",
}


def flink_runtime_api_payload() -> dict:
    op_ns = (settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
    img = (settings.FLINK_OPERATOR_IMAGE or settings.FLINK_K8S_APPLICATION_IMAGE or "").strip()
    op_ver = (settings.FLINK_OPERATOR_FLINK_VERSION or "v2_2").strip()
    inferred = infer_operator_flink_version_from_image(img)
    effective_op_ver = op_ver if op_ver else (inferred or "v2_2")
    display_ver = display_flink_version_from_runtime(effective_op_ver, img)
    return {
        "submit_mode": (settings.GIDO_FLINK_SUBMIT_MODE or "operator").strip().lower(),
        "legacy_flink_submit_enabled": bool(settings.GIDO_LEGACY_FLINK_SUBMIT),
        "flink_version": display_ver,
        "flink_operator_version": effective_op_ver,
        "supported_operator_flink_versions": supported_operator_flink_versions_public(),
        "operator_namespace": op_ns,
        "runtime_image": img or "gido-flink-runtime",
        "runtime_image_aliases": ["gido-flink-runtime"],
        "paimon_warehouse_default": (settings.PAIMON_WAREHOUSE_DEFAULT or "").strip() or None,
        "checkpoint_dir_default": (settings.FLINK_OPERATOR_CHECKPOINT_DIR or "").strip() or None,
        "sql_runner": SQL_RUNNER_INFO,
        "connectors": BUNDLED_CONNECTORS,
        "connectors_scope": "platform_default_runtime_2_2",
        "operator_profiles_supported": True,
        "runtime_image_job_override_keys": ["operator_runtime_image", "runtime_image"],
        "operator_flink_version_job_override_keys": ["operator_flink_version", "flink_version"],
        "cdc_flink_compatibility_note": CDC_FLINK_COMPATIBILITY_NOTE,
    }
