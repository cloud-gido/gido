# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""JAR 制品本地存储 + 可选 S3 持久化；Operator 通过 HTTP 或 s3:// jarURI 拉取。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
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


@dataclass(frozen=True)
class JarSaveResult:
    path: Path
    s3_uri: Optional[str] = None
    s3_sync_error: Optional[str] = None

    @property
    def s3_synced(self) -> bool:
        return self.s3_uri is not None


def artifact_dir_for_job(job_id: int) -> Path:
    base = Path(settings.JAR_ARTIFACT_DIR).expanduser().resolve()
    d = base / str(int(job_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_file_path(job_id: int) -> Path:
    return artifact_dir_for_job(job_id) / JAR_ARTIFACT_FILENAME


def save_jar_bytes(job_id: int, content: bytes) -> JarSaveResult:
    """写入本地制品库；若配置了 S3 前缀则尝试同步（失败不抛错，由调用方返回 warning）。"""
    path = artifact_file_path(job_id)
    path.write_bytes(content)
    s3_uri: Optional[str] = None
    s3_err: Optional[str] = None
    if artifact_s3_enabled():
        try:
            s3_uri = upload_artifact_bytes(
                job_id,
                JAR_ARTIFACT_FILENAME,
                content,
                content_type="application/java-archive",
            )
        except Exception as ex:
            s3_err = str(ex)
            logger.error("JAR 上传 S3 失败 job=%s: %s", job_id, ex)
    return JarSaveResult(path=path, s3_uri=s3_uri, s3_sync_error=s3_err)


def jar_artifact_exists(job_id: int) -> bool:
    p = artifact_file_path(job_id)
    if p.is_file() and p.stat().st_size > 0:
        return True
    if artifact_s3_enabled():
        return artifact_exists_in_s3(job_id, JAR_ARTIFACT_FILENAME)
    return False


def resolved_artifact_download_token() -> str:
    """Operator Pod 拉取 artifact.jar 的 query token（须稳定，勿用会随容器重启变化的 INTERNAL_TOKEN）。"""
    tok = (settings.FLINK_OPERATOR_ARTIFACT_TOKEN or "").strip()
    if tok:
        return tok
    # 与 SECRET_KEY 绑定、容器重启不变（INTERNAL_TOKEN/JWT 会在 lifespan 中轮换，不能用于 jarURI）
    return (settings.SECRET_KEY or "gido")[:32]


def artifact_download_token_is_valid(token: str) -> bool:
    t = (token or "").strip()
    if not t:
        return False
    if t == resolved_artifact_download_token():
        return True
    # 兼容旧版 jarURI 中嵌入的长期 INTERNAL JWT（容器重启前已提交的 FlinkDeployment）
    if len(t) > 40 and t.count(".") >= 2:
        try:
            from jose import JWTError, jwt

            payload = jwt.decode(t, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            return bool(payload.get("sub"))
        except JWTError:
            pass
    return False


def build_jar_http_uri_for_operator(job_id: int) -> str:
    """Flink Operator job.jarURI：集群内 Pod 须能访问该 URL（Docker 默认可解析 host.docker.internal）。"""
    base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "未配置 FLINK_OPERATOR_JAR_HTTP_BASE（Flink 集群拉取 JAR 的 GIDO API 基址，"
            "Docker 示例：http://host.docker.internal:8001）。"
        )
    token = quote(resolved_artifact_download_token(), safe="")
    return f"{base}/api/streaming/jobs/{int(job_id)}/artifact.jar?token={token}"


def build_jar_s3_uri_for_operator(job_id: int) -> Optional[str]:
    """配置 FLINK_OPERATOR_JAR_S3_PREFIX / GIDO_ARTIFACT_S3_PREFIX 时返回 s3://…"""
    return build_s3_artifact_uri(job_id, JAR_ARTIFACT_FILENAME)


def future_s3_uri_hint(job_id: int) -> Optional[str]:
    """兼容旧调用方；同 build_jar_s3_uri_for_operator。"""
    return build_jar_s3_uri_for_operator(job_id)


def resolve_jar_uri_for_operator(job_id: int) -> str:
    """S3 对象已存在时用 s3://；否则 HTTP（避免前缀已配但上传 S3 失败时提交到空对象）。"""
    s3_uri = build_jar_s3_uri_for_operator(job_id)
    if s3_uri and artifact_exists_in_s3(job_id, JAR_ARTIFACT_FILENAME):
        return s3_uri
    return build_jar_http_uri_for_operator(job_id)
