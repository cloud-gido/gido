# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""统一 Flink 运行时镜像内预置连接器清单（与 k8s/flink-runtime/connectors.manifest 对齐）。"""

from typing import List

from app.core.config import settings

BUNDLED_CONNECTORS: List[dict] = [
    {
        "id": "paimon",
        "name": "Apache Paimon",
        "artifact": "org.apache.paimon:paimon-flink-2.0",
        "version": "1.3.2",
        "path": "/opt/flink/lib/paimon-flink-2.0-1.3.2.jar",
    },
    {
        "id": "mysql-cdc",
        "name": "Flink CDC MySQL",
        "artifact": "org.apache.flink:flink-sql-connector-mysql-cdc",
        "version": "3.5.0",
        "connector": "mysql-cdc",
    },
    {
        "id": "postgres-cdc",
        "name": "Flink CDC PostgreSQL",
        "artifact": "org.apache.flink:flink-sql-connector-postgres-cdc",
        "version": "3.5.0",
        "connector": "postgres-cdc",
    },
    {
        "id": "s3-fs-hadoop",
        "name": "Flink S3 Filesystem (Hadoop)",
        "artifact": "org.apache.flink:flink-s3-fs-hadoop",
        "version": "2.0.1",
        "path": "/opt/flink/plugins/s3-fs-hadoop/flink-s3-fs-hadoop-2.0.1.jar",
        "scheme": "s3://",
    },
]

CDC_FLINK_COMPATIBILITY_NOTE = (
    "Flink CDC 3.6+ 在 Maven 为 3.6.0-1.20 / 3.6.0-2.2；GIDO Flink 2.0.1 预置 3.5.0。"
    "CDC→Paimon 链路请在目标环境验证，或升级 Flink 至 2.2.x 后改用 3.6.0-2.2。"
)

SQL_RUNNER_INFO = {
    "path": "/opt/flink/usrlib/sql-runner.jar",
    "entry_class": "com.gido.flink.SqlRunner",
    "artifact": "com.gido:flink-sql-runner:1.0.0",
}


def flink_runtime_api_payload() -> dict:
    op_ns = (settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
    img = (settings.FLINK_OPERATOR_IMAGE or settings.FLINK_K8S_APPLICATION_IMAGE or "").strip()
    return {
        "submit_mode": (settings.GIDO_FLINK_SUBMIT_MODE or "operator").strip().lower(),
        "legacy_flink_submit_enabled": bool(settings.GIDO_LEGACY_FLINK_SUBMIT),
        "flink_version": "2.0.1",
        "flink_operator_version": (settings.FLINK_OPERATOR_FLINK_VERSION or "v2_0").strip(),
        "operator_namespace": op_ns,
        "runtime_image": img or "gido-flink-runtime",
        "runtime_image_aliases": ["gido-flink-runtime"],
        "paimon_warehouse_default": (settings.PAIMON_WAREHOUSE_DEFAULT or "").strip() or None,
        "checkpoint_dir_default": (settings.FLINK_OPERATOR_CHECKPOINT_DIR or "").strip() or None,
        "sql_runner": SQL_RUNNER_INFO,
        "connectors": BUNDLED_CONNECTORS,
        "cdc_flink_compatibility_note": CDC_FLINK_COMPATIBILITY_NOTE,
    }
