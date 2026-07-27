# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""JAR 制品本地存储 + 可选 S3 持久化；Operator 通过 HTTP 或 s3:// jarURI 拉取。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from app.core.config import settings
from app.services.artifact_s3 import (
    JAR_ARTIFACT_FILENAME,
    artifact_exists_in_s3,
    artifact_s3_enabled,
    build_s3_artifact_uri,
    upload_artifact_bytes,
)

logger = logging.getLogger(__name__)


def artifact_dir_for_job(job_id: int) -> Path:
    base = Path(settings.JAR_ARTIFACT_DIR).expanduser().resolve()
    d = base / str(int(job_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_file_path(job_id: int) -> Path:
    return artifact_dir_for_job(job_id) / JAR_ARTIFACT_FILENAME


def save_jar_bytes(job_id: int, content: bytes) -> Path:
    path = artifact_file_path(job_id)
    path.write_bytes(content)
    if artifact_s3_enabled():
        try:
            upload_artifact_bytes(
                job_id,
                JAR_ARTIFACT_FILENAME,
                content,
                content_type="application/java-archive",
            )
        except Exception as ex:
            logger.error("JAR 上传 S3 失败 job=%s: %s", job_id, ex)
            raise RuntimeError(f"JAR 已写入本地但上传 S3 失败: {ex}") from ex
    return path


def jar_artifact_exists(job_id: int) -> bool:
    p = artifact_file_path(job_id)
    if p.is_file() and p.stat().st_size > 0:
        return True
    if artifact_s3_enabled():
        return artifact_exists_in_s3(job_id, JAR_ARTIFACT_FILENAME)
    return False


def library_version_dir(artifact_id: int, version: int) -> Path:
    base = Path(settings.JAR_ARTIFACT_DIR).expanduser().resolve()
    d = base / "library" / str(int(artifact_id)) / f"v{int(version)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def library_version_file_path(artifact_id: int, version: int) -> Path:
    return library_version_dir(artifact_id, version) / JAR_ARTIFACT_FILENAME


def save_library_jar_bytes(artifact_id: int, version: int, content: bytes) -> Path:
    path = library_version_file_path(artifact_id, version)
    path.write_bytes(content)
    if artifact_s3_enabled():
        try:
            from app.services.artifact_s3 import upload_artifact_bytes_at_key, s3_key_for_library_version

            key = s3_key_for_library_version(artifact_id, version, JAR_ARTIFACT_FILENAME)
            if key:
                upload_artifact_bytes_at_key(key, content, content_type="application/java-archive")
        except Exception as ex:
            logger.error("JAR 库版本上传 S3 失败 artifact=%s v=%s: %s", artifact_id, version, ex)
            raise RuntimeError(f"JAR 已写入本地但上传 S3 失败: {ex}") from ex
    return path


def library_jar_exists(artifact_id: int, version: int) -> bool:
    p = library_version_file_path(artifact_id, version)
    if p.is_file() and p.stat().st_size > 0:
        return True
    if artifact_s3_enabled():
        from app.services.artifact_s3 import artifact_exists_in_s3_key, s3_key_for_library_version

        key = s3_key_for_library_version(artifact_id, version, JAR_ARTIFACT_FILENAME)
        return bool(key and artifact_exists_in_s3_key(key))
    return False


def resolved_artifact_download_token() -> str:
    """Operator Pod 拉取 artifact.jar 的 query token（须稳定，勿用会随容器重启变化的 INTERNAL_TOKEN）。"""
    tok = (settings.FLINK_OPERATOR_ARTIFACT_TOKEN or "").strip()
    if tok:
        return tok
    return (settings.SECRET_KEY or "gido")[:32]


def artifact_download_token_is_valid(token: str) -> bool:
    t = (token or "").strip()
    if not t:
        return False
    if t == resolved_artifact_download_token():
        return True
    if len(t) > 40 and t.count(".") >= 2:
        try:
            from jose import JWTError, jwt

            payload = jwt.decode(t, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            return bool(payload.get("sub"))
        except JWTError:
            pass
    return False


def build_jar_http_uri_for_operator(job_id: int) -> str:
    base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "未配置 FLINK_OPERATOR_JAR_HTTP_BASE（Flink 集群拉取 JAR 的 GIDO API 基址，"
            "Docker 示例：http://host.docker.internal:8001）。"
        )
    token = quote(resolved_artifact_download_token(), safe="")
    return f"{base}/api/streaming/jobs/{int(job_id)}/artifact.jar?token={token}"


def build_jar_s3_uri_for_operator(job_id: int) -> Optional[str]:
    return build_s3_artifact_uri(job_id, JAR_ARTIFACT_FILENAME)


def future_s3_uri_hint(job_id: int) -> Optional[str]:
    return build_jar_s3_uri_for_operator(job_id)


def build_library_jar_http_uri_for_operator(version_id: int) -> str:
    base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "未配置 FLINK_OPERATOR_JAR_HTTP_BASE（Flink 集群拉取 JAR 的 GIDO API 基址，"
            "Docker 示例：http://host.docker.internal:8001）。"
        )
    token = quote(resolved_artifact_download_token(), safe="")
    return f"{base}/api/streaming/jar-versions/{int(version_id)}/artifact.jar?token={token}"


def build_library_jar_s3_uri(artifact_id: int, version: int) -> Optional[str]:
    from app.services.artifact_s3 import build_s3_library_version_uri

    return build_s3_library_version_uri(artifact_id, version, JAR_ARTIFACT_FILENAME)


def resolve_jar_uri_for_operator(
    job_id: int,
    jar_version_id: Optional[int] = None,
    artifact_id: Optional[int] = None,
    version_num: Optional[int] = None,
) -> str:
    """优先制品库版本；否则回退作业本地制品。"""
    if jar_version_id and artifact_id is not None and version_num is not None:
        s3_uri = build_library_jar_s3_uri(int(artifact_id), int(version_num))
        if s3_uri:
            return s3_uri
        return build_library_jar_http_uri_for_operator(int(jar_version_id))
    s3_uri = build_jar_s3_uri_for_operator(job_id)
    if s3_uri:
        return s3_uri
    return build_jar_http_uri_for_operator(job_id)
