# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""Flink Operator JAR/SQL 制品 S3 持久化（平台默认 + 各 Operator 集群 Profile 前缀）。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.operator_runtime import OperatorRuntimeContext

logger = logging.getLogger(__name__)

JAR_ARTIFACT_FILENAME = "artifact.jar"
SQL_ARTIFACT_FILENAME = "artifact.sql"

_UNRESOLVED_PREFIX_MARKERS = frozenset(
    {
        "FLINK_OPERATOR_JAR_S3_PREFIX",
        "GIDO_ARTIFACT_S3_PREFIX",
        "GIDO_S3_BUCKET",
        "__FLINK_OPERATOR_JAR_S3_PREFIX__",
        "__GIDO_ARTIFACT_S3_PREFIX__",
    }
)


def _looks_like_unresolved_s3_prefix(raw: str) -> bool:
    s = raw.strip()
    if not s:
        return False
    if s in _UNRESOLVED_PREFIX_MARKERS:
        return True
    if s.startswith("__") and s.endswith("__"):
        return True
    if "CHANGE_ME" in s.upper():
        return True
    return False


def _normalize_s3_prefix(raw: Optional[str]) -> Optional[str]:
    prefix = (raw or "").strip().rstrip("/")
    if not prefix:
        return None
    if not prefix.lower().startswith("s3://"):
        raise ValueError(f"artifact S3 prefix 须以 s3:// 开头: {prefix}")
    return prefix


def _platform_artifact_s3_prefix() -> Optional[str]:
    for raw in (
        getattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", None),
        getattr(settings, "GIDO_ARTIFACT_S3_PREFIX", None),
    ):
        p = _try_normalize_s3_prefix(raw)
        if p:
            return p
    return None


def _try_normalize_s3_prefix(raw: Optional[str]) -> Optional[str]:
    """无效或未替换占位符返回 None，不抛异常。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if _looks_like_unresolved_s3_prefix(s):
        logger.warning("忽略未解析的 S3 前缀占位符: %s", s)
        return None
    try:
        return _normalize_s3_prefix(s)
    except ValueError as ex:
        logger.warning("忽略无效 S3 前缀: %s", ex)
        return None


def s3_prefix_config_hint(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> Optional[str]:
    """前缀未配置或无效时返回面向运维/用户的说明。"""
    raw: Optional[str] = None
    source = ""
    if runtime_ctx is not None:
        prof_raw = getattr(runtime_ctx, "jar_s3_prefix", None)
        if prof_raw and str(prof_raw).strip():
            raw = str(prof_raw).strip()
            name = getattr(runtime_ctx, "profile_name", None) or getattr(runtime_ctx, "profile_id", None)
            source = f"Operator 集群「{name}」JAR 制品 S3 前缀" if name else "Operator 集群 JAR 制品 S3 前缀"
    if raw is None:
        for key in ("FLINK_OPERATOR_JAR_S3_PREFIX", "GIDO_ARTIFACT_S3_PREFIX"):
            val = getattr(settings, key, None)
            if val and str(val).strip():
                raw = str(val).strip()
                source = f"平台环境变量 {key}"
                break
    if raw is None:
        return (
            "未配置 JAR 制品 S3 前缀。请在 ConfigMap gido-backend-config 设置 "
            "FLINK_OPERATOR_JAR_S3_PREFIX=s3://<bucket>/<prefix>，或在 Operator 集群 Profile 中配置。"
        )
    if _looks_like_unresolved_s3_prefix(raw) or not raw.lower().startswith("s3://"):
        return (
            f"{source} 无效（当前值: {raw!r}）。须为 s3://<bucket>/<prefix>，"
            "例如 s3://flink-on-devtest/gido-flink；勿填写环境变量名或未替换的 YAML 占位符。"
        )
    return None


def artifact_s3_prefix(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> Optional[str]:
    """Profile jar_s3_prefix 优先；无效则回退平台 FLINK_OPERATOR_JAR_S3_PREFIX / GIDO_ARTIFACT_S3_PREFIX。"""
    if runtime_ctx is not None:
        prof = _try_normalize_s3_prefix(getattr(runtime_ctx, "jar_s3_prefix", None))
        if prof:
            return prof
    return _platform_artifact_s3_prefix()


def artifact_s3_enabled(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> bool:
    return artifact_s3_prefix(runtime_ctx) is not None


def _parse_s3_prefix(uri: str) -> Tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"无效 S3 URI: {uri}")
    bucket = parsed.netloc
    key_prefix = (parsed.path or "").strip("/")
    return bucket, key_prefix


def s3_key_for_artifact(
    job_id: int,
    filename: str,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> Optional[str]:
    prefix = artifact_s3_prefix(runtime_ctx)
    if not prefix:
        return None
    _, key_prefix = _parse_s3_prefix(prefix)
    parts = [p for p in (key_prefix, str(int(job_id)), filename) if p]
    return "/".join(parts)


def build_s3_artifact_uri(
    job_id: int,
    filename: str,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> Optional[str]:
    prefix = artifact_s3_prefix(runtime_ctx)
    if not prefix:
        return None
    key = s3_key_for_artifact(job_id, filename, runtime_ctx=runtime_ctx)
    if not key:
        return None
    bucket, _ = _parse_s3_prefix(prefix)
    return f"s3://{bucket}/{key}"


def _s3_client(runtime_ctx: Optional["OperatorRuntimeContext"] = None):
    import boto3

    from app.services.s3_auth import boto3_client_kwargs

    return boto3.client("s3", **boto3_client_kwargs(runtime_ctx))


def upload_artifact_bytes(
    job_id: int,
    filename: str,
    content: bytes,
    *,
    content_type: Optional[str] = None,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> str:
    """上传制品到 S3；返回 s3:// URI。"""
    prefix = artifact_s3_prefix(runtime_ctx)
    if not prefix:
        raise RuntimeError("未配置 JAR 制品 S3 前缀（Operator 集群或平台 FLINK_OPERATOR_JAR_S3_PREFIX）")
    bucket, _ = _parse_s3_prefix(prefix)
    key = s3_key_for_artifact(job_id, filename, runtime_ctx=runtime_ctx)
    if not key:
        raise RuntimeError("无法解析 S3 artifact key")
    extra: dict = {}
    if content_type:
        extra["ContentType"] = content_type
    client = _s3_client(runtime_ctx)
    client.put_object(Bucket=bucket, Key=key, Body=content, **extra)
    uri = f"s3://{bucket}/{key}"
    logger.info("已上传制品到 S3 job=%s key=%s", job_id, key)
    return uri


def artifact_exists_in_s3(
    job_id: int,
    filename: str,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> bool:
    prefix = artifact_s3_prefix(runtime_ctx)
    if not prefix:
        return False
    bucket, _ = _parse_s3_prefix(prefix)
    key = s3_key_for_artifact(job_id, filename, runtime_ctx=runtime_ctx)
    if not key:
        return False
    try:
        _s3_client(runtime_ctx).head_object(Bucket=bucket, Key=key)
        return True
    except Exception as ex:
        code = getattr(ex, "response", {}).get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        logger.debug("S3 head_object 失败 job=%s key=%s: %s", job_id, key, ex)
        return False


def _iso_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _s3_object_row(bucket: str, obj: Dict[str, Any]) -> Dict[str, Any]:
    key = str(obj.get("Key") or "")
    return {
        "key": key,
        "uri": f"s3://{bucket}/{key}" if key else None,
        "size_bytes": int(obj.get("Size") or 0),
        "last_modified": _iso_datetime(obj.get("LastModified")),
        "etag": (obj.get("ETag") or "").strip('"') or None,
    }


def list_s3_objects_under_key_prefix(
    bucket: str,
    key_prefix: str,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    max_keys: int = 200,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """列出 bucket 下 key_prefix/ 内对象（不含目录占位 key）。"""
    prefix = (key_prefix or "").strip("/")
    if prefix:
        prefix = f"{prefix}/"
    client = _s3_client(runtime_ctx)
    rows: List[Dict[str, Any]] = []
    token: Optional[str] = None
    try:
        while len(rows) < max_keys:
            kwargs: Dict[str, Any] = {
                "Bucket": bucket,
                "Prefix": prefix,
                "MaxKeys": min(1000, max_keys - len(rows)),
            }
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents") or []:
                key = str(obj.get("Key") or "")
                if not key or key.endswith("/"):
                    continue
                rows.append(_s3_object_row(bucket, obj))
                if len(rows) >= max_keys:
                    break
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break
        return rows, None
    except Exception as ex:
        logger.warning("S3 list_objects_v2 失败 bucket=%s prefix=%s: %s", bucket, prefix, ex)
        return [], str(ex)


def list_s3_job_folder_prefixes(
    bucket: str,
    key_prefix: str,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    max_folders: int = 100,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """列出集群制品根目录下各 job_id 子目录（CommonPrefixes）。"""
    root = (key_prefix or "").strip("/")
    if root:
        root = f"{root}/"
    client = _s3_client(runtime_ctx)
    folders: List[Dict[str, Any]] = []
    token: Optional[str] = None
    try:
        while len(folders) < max_folders:
            kwargs: Dict[str, Any] = {
                "Bucket": bucket,
                "Prefix": root,
                "Delimiter": "/",
                "MaxKeys": min(1000, max_folders - len(folders)),
            }
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            for cp in resp.get("CommonPrefixes") or []:
                p = str(cp.get("Prefix") or "")
                if not p:
                    continue
                job_part = p[len(root) :].strip("/").split("/")[0] if p.startswith(root) else p.strip("/")
                folders.append(
                    {
                        "prefix": p,
                        "job_id": job_part if job_part.isdigit() else None,
                        "uri": f"s3://{bucket}/{p.rstrip('/')}",
                    }
                )
                if len(folders) >= max_folders:
                    break
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break
        return folders, None
    except Exception as ex:
        logger.warning("S3 list job folders 失败 bucket=%s prefix=%s: %s", bucket, root, ex)
        return [], str(ex)


def artifact_s3_prefix_source(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> Optional[str]:
    if runtime_ctx is not None and getattr(runtime_ctx, "jar_s3_prefix", None):
        if _try_normalize_s3_prefix(getattr(runtime_ctx, "jar_s3_prefix", None)):
            return "profile"
    if _platform_artifact_s3_prefix():
        return "platform"
    return None


def artifact_s3_region_source(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> Optional[str]:
    from app.services.s3_auth import _platform_s3_region

    if runtime_ctx is not None:
        prof = (getattr(runtime_ctx, "s3_region", None) or "").strip()
        if prof:
            return "profile"
    if _platform_s3_region():
        return "platform"
    return None


def artifact_s3_endpoint_source(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> Optional[str]:
    from app.services.s3_auth import _platform_s3_endpoint

    if runtime_ctx is not None:
        prof = (getattr(runtime_ctx, "s3_endpoint_url", None) or "").strip()
        if prof:
            return "profile"
    if _platform_s3_endpoint():
        return "platform"
    return None
