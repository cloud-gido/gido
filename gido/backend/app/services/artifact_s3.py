# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""Flink Operator JAR/SQL 制品 S3 持久化（EKS IRSA / 默认凭证链）。"""
from __future__ import annotations

import logging
from typing import Optional, Tuple
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

JAR_ARTIFACT_FILENAME = "artifact.jar"
SQL_ARTIFACT_FILENAME = "artifact.sql"


def artifact_s3_prefix() -> Optional[str]:
    """FLINK_OPERATOR_JAR_S3_PREFIX 优先；GIDO_ARTIFACT_S3_PREFIX 为别名。"""
    for raw in (
        getattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", None),
        getattr(settings, "GIDO_ARTIFACT_S3_PREFIX", None),
    ):
        prefix = (raw or "").strip().rstrip("/")
        if prefix:
            if not prefix.lower().startswith("s3://"):
                raise ValueError(f"artifact S3 prefix 须以 s3:// 开头: {prefix}")
            return prefix
    return None


def artifact_s3_enabled() -> bool:
    return artifact_s3_prefix() is not None


def _parse_s3_prefix(uri: str) -> Tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"无效 S3 URI: {uri}")
    bucket = parsed.netloc
    key_prefix = (parsed.path or "").strip("/")
    return bucket, key_prefix


def s3_key_for_artifact(job_id: int, filename: str) -> Optional[str]:
    prefix = artifact_s3_prefix()
    if not prefix:
        return None
    _, key_prefix = _parse_s3_prefix(prefix)
    parts = [p for p in (key_prefix, str(int(job_id)), filename) if p]
    return "/".join(parts)


def build_s3_artifact_uri(job_id: int, filename: str) -> Optional[str]:
    prefix = artifact_s3_prefix()
    if not prefix:
        return None
    key = s3_key_for_artifact(job_id, filename)
    if not key:
        return None
    bucket, _ = _parse_s3_prefix(prefix)
    return f"s3://{bucket}/{key}"


def _s3_client():
    import boto3

    kwargs = {}
    region = (getattr(settings, "GIDO_ARTIFACT_S3_REGION", None) or "").strip()
    if region:
        kwargs["region_name"] = region
    endpoint = (getattr(settings, "GIDO_ARTIFACT_S3_ENDPOINT_URL", None) or "").strip()
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **kwargs)


def upload_artifact_bytes(
    job_id: int,
    filename: str,
    content: bytes,
    *,
    content_type: Optional[str] = None,
) -> str:
    """上传制品到 S3；返回 s3:// URI。"""
    prefix = artifact_s3_prefix()
    if not prefix:
        raise RuntimeError("未配置 FLINK_OPERATOR_JAR_S3_PREFIX 或 GIDO_ARTIFACT_S3_PREFIX")
    bucket, _ = _parse_s3_prefix(prefix)
    key = s3_key_for_artifact(job_id, filename)
    if not key:
        raise RuntimeError("无法解析 S3 artifact key")
    extra: dict = {}
    if content_type:
        extra["ContentType"] = content_type
    client = _s3_client()
    client.put_object(Bucket=bucket, Key=key, Body=content, **extra)
    uri = f"s3://{bucket}/{key}"
    logger.info("已上传制品到 S3 job=%s key=%s", job_id, key)
    return uri


def artifact_exists_in_s3(job_id: int, filename: str) -> bool:
    prefix = artifact_s3_prefix()
    if not prefix:
        return False
    bucket, _ = _parse_s3_prefix(prefix)
    key = s3_key_for_artifact(job_id, filename)
    if not key:
        return False
    try:
        _s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception as ex:
        code = getattr(ex, "response", {}).get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        logger.debug("S3 head_object 失败 job=%s key=%s: %s", job_id, key, ex)
        return False


def s3_key_for_library_version(artifact_id: int, version: int, filename: str) -> Optional[str]:
    prefix = artifact_s3_prefix()
    if not prefix:
        return None
    _, key_prefix = _parse_s3_prefix(prefix)
    parts = [p for p in (key_prefix, "library", str(int(artifact_id)), f"v{int(version)}", filename) if p]
    return "/".join(parts)


def s3_key_for_kind_library_version(kind: str, artifact_id: int, version: int, filename: str) -> Optional[str]:
    prefix = artifact_s3_prefix()
    if not prefix:
        return None
    k = (kind or "").strip().strip("/")
    _, key_prefix = _parse_s3_prefix(prefix)
    parts = [p for p in (key_prefix, "library", k, str(int(artifact_id)), f"v{int(version)}", filename) if p]
    return "/".join(parts)


def build_s3_library_version_uri(artifact_id: int, version: int, filename: str) -> Optional[str]:
    prefix = artifact_s3_prefix()
    if not prefix:
        return None
    key = s3_key_for_library_version(artifact_id, version, filename)
    if not key:
        return None
    bucket, _ = _parse_s3_prefix(prefix)
    return f"s3://{bucket}/{key}"


def build_s3_kind_library_version_uri(kind: str, artifact_id: int, version: int, filename: str) -> Optional[str]:
    prefix = artifact_s3_prefix()
    if not prefix:
        return None
    key = s3_key_for_kind_library_version(kind, artifact_id, version, filename)
    if not key:
        return None
    bucket, _ = _parse_s3_prefix(prefix)
    return f"s3://{bucket}/{key}"


def upload_artifact_bytes_at_key(
    key: str,
    content: bytes,
    *,
    content_type: Optional[str] = None,
) -> str:
    prefix = artifact_s3_prefix()
    if not prefix:
        raise RuntimeError("未配置 FLINK_OPERATOR_JAR_S3_PREFIX 或 GIDO_ARTIFACT_S3_PREFIX")
    bucket, _ = _parse_s3_prefix(prefix)
    extra: dict = {}
    if content_type:
        extra["ContentType"] = content_type
    _s3_client().put_object(Bucket=bucket, Key=key, Body=content, **extra)
    uri = f"s3://{bucket}/{key}"
    logger.info("已上传制品到 S3 key=%s", key)
    return uri


def download_artifact_bytes_at_key(key: str) -> Optional[bytes]:
    prefix = artifact_s3_prefix()
    if not prefix:
        return None
    bucket, _ = _parse_s3_prefix(prefix)
    try:
        response = _s3_client().get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except Exception as ex:
        code = getattr(ex, "response", {}).get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def artifact_exists_in_s3_key(key: str) -> bool:
    prefix = artifact_s3_prefix()
    if not prefix:
        return False
    bucket, _ = _parse_s3_prefix(prefix)
    try:
        _s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception as ex:
        code = getattr(ex, "response", {}).get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        logger.debug("S3 head_object 失败 key=%s: %s", key, ex)
        return False


def shared_object_key(namespace: str, filename: str) -> str:
    prefix = artifact_s3_prefix()
    if not prefix:
        raise RuntimeError("共享对象存储需要配置 FLINK_OPERATOR_JAR_S3_PREFIX 或 GIDO_ARTIFACT_S3_PREFIX")
    _, key_prefix = _parse_s3_prefix(prefix)
    return "/".join(
        part for part in (key_prefix, namespace.strip("/"), filename.strip("/")) if part
    )


def put_shared_object(namespace: str, filename: str, content: bytes, content_type: str) -> None:
    prefix = artifact_s3_prefix()
    if not prefix:
        raise RuntimeError("共享对象存储 S3 未配置")
    bucket, _ = _parse_s3_prefix(prefix)
    _s3_client().put_object(
        Bucket=bucket,
        Key=shared_object_key(namespace, filename),
        Body=content,
        ContentType=content_type,
    )


def get_shared_object(namespace: str, filename: str) -> Optional[bytes]:
    prefix = artifact_s3_prefix()
    if not prefix:
        return None
    bucket, _ = _parse_s3_prefix(prefix)
    try:
        response = _s3_client().get_object(
            Bucket=bucket,
            Key=shared_object_key(namespace, filename),
        )
        return response["Body"].read()
    except Exception as ex:
        code = getattr(ex, "response", {}).get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def delete_shared_object(namespace: str, filename: str) -> None:
    prefix = artifact_s3_prefix()
    if not prefix:
        return
    bucket, _ = _parse_s3_prefix(prefix)
    _s3_client().delete_object(
        Bucket=bucket,
        Key=shared_object_key(namespace, filename),
    )
