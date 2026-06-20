# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""JAR 制品：Profile S3 统一存储；按 Flink 版本 Direct S3 或 presigned local staging。"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Literal, Optional
from urllib.parse import quote

from app.core.config import settings
from app.services.artifact_s3 import (
    JAR_ARTIFACT_FILENAME,
    artifact_exists_in_s3,
    artifact_s3_enabled,
    artifact_s3_prefix,
    build_s3_artifact_uri,
    presign_s3_artifact_get_url,
    s3_prefix_config_hint,
    upload_artifact_bytes,
)
from app.services.nexus_artifact import fetch_jar_bytes_from_nexus

if TYPE_CHECKING:
    from app.services.operator_runtime import OperatorRuntimeContext

logger = logging.getLogger(__name__)

JarDeliveryMode = Literal["direct_s3", "local_staging", "http_fallback"]

_SAFE_JAR_BASENAME = re.compile(r"^[A-Za-z0-9._-]+\.jar$", re.IGNORECASE)


@dataclass(frozen=True)
class JarSaveResult:
    path: Path
    storage_filename: str = JAR_ARTIFACT_FILENAME


@dataclass(frozen=True)
class S3ArtifactRef:
    uri: str
    sha256: str
    skipped_upload: bool = False


@dataclass(frozen=True)
class JarSubmitArtifacts:
    delivery_mode: JarDeliveryMode
    jar_uri: str
    s3_uri: Optional[str] = None
    staging_fetch_url: Optional[str] = None
    http_download_uri: Optional[str] = None
    uses_local_staging: bool = False
    local_staged_path: Optional[str] = None
    sha256: Optional[str] = None


def flink_jar_needs_local_staging(flink_version: Optional[str]) -> bool:
    """Flink 1.17/1.18 Application 模式主 JAR 须 local://（远程 jarURI 自 1.19 起完善）。"""
    v = (flink_version or "").strip().lower()
    if not v:
        return False
    if v.startswith("v2_"):
        return False
    if v in ("v1_19", "v1_20", "v1_21", "v1_22"):
        return False
    if v.startswith("v1_"):
        return True
    return False


def resolve_jar_delivery_mode(flink_version: Optional[str]) -> JarDeliveryMode:
    if flink_jar_needs_local_staging(flink_version):
        return "local_staging"
    return "direct_s3"


def _jar_staging_local_uri() -> str:
    mount = (settings.FLINK_OPERATOR_JAR_STAGING_MOUNT or "/opt/flink/usrlib/gido-artifacts").strip()
    return f"local://{mount.rstrip('/')}/job.jar"


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def jar_storage_filename_from_jar_path(jar_path: Optional[str]) -> Optional[str]:
    """从 job.jar_path（上传原始名或 Session jar id）提取可安全用作存储文件名的 basename。"""
    if not jar_path:
        return None
    base = Path(str(jar_path).strip()).name
    if _SAFE_JAR_BASENAME.match(base):
        return base
    return None


def _job_artifact_dir_readonly(job_id: int) -> Path:
    base = Path(settings.JAR_ARTIFACT_DIR).expanduser().resolve()
    return base / str(int(job_id))


def resolve_jar_storage_filename(job_id: int, *, jar_path: Optional[str] = None) -> str:
    """解析作业目录内实际 JAR 文件名；兼容历史 artifact.jar。"""
    d = _job_artifact_dir_readonly(job_id)
    if d.is_dir():
        jars = [p for p in d.glob("*.jar") if p.is_file() and p.stat().st_size > 0]
        if jars:
            hint = jar_storage_filename_from_jar_path(jar_path)
            if hint:
                for p in jars:
                    if p.name == hint:
                        return hint
            if len(jars) == 1:
                return jars[0].name
            return max(jars, key=lambda p: p.stat().st_mtime).name
    hint = jar_storage_filename_from_jar_path(jar_path)
    return hint or JAR_ARTIFACT_FILENAME


def artifact_dir_for_job(job_id: int) -> Path:
    base = Path(settings.JAR_ARTIFACT_DIR).expanduser().resolve()
    d = base / str(int(job_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_file_path(job_id: int, *, storage_filename: Optional[str] = None) -> Path:
    fn = storage_filename or JAR_ARTIFACT_FILENAME
    return artifact_dir_for_job(job_id) / fn


def resolve_artifact_file_path(job_id: int, *, jar_path: Optional[str] = None) -> Path:
    fn = resolve_jar_storage_filename(job_id, jar_path=jar_path)
    return _job_artifact_dir_readonly(job_id) / fn


def artifact_file_path_readonly(job_id: int, *, jar_path: Optional[str] = None) -> Path:
    """清单/探测用：不创建目录，避免只读或权限异常导致 API 失败。"""
    return resolve_artifact_file_path(job_id, jar_path=jar_path)


def jar_artifact_exists_local(
    job_id: int,
    *,
    jar_path: Optional[str] = None,
) -> bool:
    p = resolve_artifact_file_path(job_id, jar_path=jar_path)
    return p.is_file() and p.stat().st_size > 0


def save_jar_bytes(
    job_id: int,
    content: bytes,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    original_filename: Optional[str] = None,
) -> JarSaveResult:
    """写入 backend 本地制品库（可选镜像；生产以 Profile S3 为准）。"""
    storage_fn = jar_storage_filename_from_jar_path(original_filename) or JAR_ARTIFACT_FILENAME
    d = artifact_dir_for_job(job_id)
    for old in d.glob("*.jar"):
        if old.name != storage_fn:
            try:
                old.unlink()
            except OSError as ex:
                logger.warning("清理旧 JAR 制品失败 job=%s path=%s: %s", job_id, old, ex)
    path = d / storage_fn
    path.write_bytes(content)
    if runtime_ctx is not None and artifact_s3_enabled(runtime_ctx):
        upload_artifact_bytes(
            job_id,
            storage_fn,
            content,
            content_type="application/java-archive",
            runtime_ctx=runtime_ctx,
        )
    return JarSaveResult(path=path, storage_filename=storage_fn)


def _load_jar_bytes_for_job(job: Any, *, jar_path: Optional[str] = None) -> tuple[bytes, str]:
    nexus_url = (getattr(job, "jar_nexus_url", None) or "").strip()
    if nexus_url:
        return fetch_jar_bytes_from_nexus(nexus_url), "nexus"
    if jar_artifact_exists_local(int(job.id), jar_path=jar_path):
        path = resolve_artifact_file_path(int(job.id), jar_path=jar_path)
        return path.read_bytes(), "local"
    raise RuntimeError("请配置 jar_nexus_url 或上传 JAR 文件")


def ensure_jar_in_profile_s3(
    job: Any,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> S3ArtifactRef:
    """提交前：Nexus/本地上传 → Profile S3（1+ 去重）。"""
    if runtime_ctx is None:
        raise RuntimeError("Operator 提交须解析 Flink Operator Profile 上下文")
    hint = s3_prefix_config_hint(runtime_ctx)
    if hint:
        raise RuntimeError(hint)
    if not artifact_s3_enabled(runtime_ctx):
        raise RuntimeError("当前 Operator 集群未配置 JAR 制品 S3 前缀")

    job_id = int(job.id)
    content, source = _load_jar_bytes_for_job(job, jar_path=getattr(job, "jar_path", None))
    sha = _sha256_bytes(content)
    storage_fn = JAR_ARTIFACT_FILENAME

    nexus_url = (getattr(job, "jar_nexus_url", None) or "").strip()
    prev_sha = (getattr(job, "jar_nexus_sha256", None) or "").strip()
    skipped = False
    if artifact_exists_in_s3(job_id, storage_fn, runtime_ctx):
        if prev_sha and sha == prev_sha:
            skipped = True

    if not skipped:
        upload_artifact_bytes(
            job_id,
            storage_fn,
            content,
            content_type="application/java-archive",
            runtime_ctx=runtime_ctx,
        )
        try:
            save_jar_bytes(job_id, content, runtime_ctx=None, original_filename=storage_fn)
        except OSError as ex:
            logger.warning("JAR 本地镜像写入失败 job=%s: %s", job_id, ex)

    uri = build_s3_artifact_uri(job_id, storage_fn, runtime_ctx=runtime_ctx)
    if not uri:
        raise RuntimeError("无法解析 JAR S3 URI")
    job.jar_nexus_sha256 = sha
    return S3ArtifactRef(uri=uri, sha256=sha, skipped_upload=skipped)


def jar_artifact_exists(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
) -> bool:
    if artifact_s3_enabled(runtime_ctx):
        if artifact_exists_in_s3(job_id, JAR_ARTIFACT_FILENAME, runtime_ctx):
            return True
    return jar_artifact_exists_local(job_id, jar_path=jar_path)


def jar_artifact_source_ready(job: Any) -> bool:
    """提交前：是否已配置 Nexus URL 或本地 JAR。"""
    if (getattr(job, "jar_nexus_url", None) or "").strip():
        return True
    return jar_artifact_exists_local(int(job.id), jar_path=getattr(job, "jar_path", None))


def resolved_artifact_download_token() -> str:
    """Operator Pod 拉取 artifact.jar 的 query token（HTTP 回退路径）。"""
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


def build_jar_http_uri_for_operator(job_id: int, *, jar_path: Optional[str] = None) -> str:
    """HTTP 回退：Flink Pod 经 GIDO Backend 拉 JAR。"""
    base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "未配置 FLINK_OPERATOR_JAR_HTTP_BASE（Flink 集群拉取 JAR 的 GIDO API 基址，"
            "K8s 示例：http://gido-backend.gido.svc.cluster.local:8001）。"
        )
    token = quote(resolved_artifact_download_token(), safe="")
    return f"{base}/api/streaming/jobs/{int(job_id)}/artifact.jar?token={token}"


def resolve_jar_submit_artifacts(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
    s3_ref: Optional[S3ArtifactRef] = None,
) -> JarSubmitArtifacts:
    fv = (
        getattr(runtime_ctx, "flink_version", None)
        if runtime_ctx is not None
        else getattr(settings, "FLINK_OPERATOR_FLINK_VERSION", None)
    )

    if s3_ref is not None:
        delivery = resolve_jar_delivery_mode(fv)
        if delivery == "direct_s3":
            return JarSubmitArtifacts(
                delivery_mode="direct_s3",
                jar_uri=s3_ref.uri,
                s3_uri=s3_ref.uri,
                staging_fetch_url=None,
                http_download_uri=None,
                uses_local_staging=False,
                local_staged_path=None,
                sha256=s3_ref.sha256,
            )
        presigned = presign_s3_artifact_get_url(
            job_id, JAR_ARTIFACT_FILENAME, runtime_ctx=runtime_ctx
        )
        local_path = _jar_staging_local_uri().removeprefix("local://")
        return JarSubmitArtifacts(
            delivery_mode="local_staging",
            jar_uri=_jar_staging_local_uri(),
            s3_uri=s3_ref.uri,
            staging_fetch_url=presigned,
            http_download_uri=presigned,
            uses_local_staging=True,
            local_staged_path=local_path,
            sha256=s3_ref.sha256,
        )

    if settings.GIDO_JAR_ARTIFACT_REQUIRE_S3:
        raise RuntimeError(
            s3_prefix_config_hint(runtime_ctx)
            or "生产环境须配置 Operator 集群 JAR 制品 S3 前缀（GIDO_JAR_ARTIFACT_REQUIRE_S3=true）"
        )

    http_uri = build_jar_http_uri_for_operator(job_id, jar_path=jar_path)
    if flink_jar_needs_local_staging(fv):
        local_path = _jar_staging_local_uri().removeprefix("local://")
        return JarSubmitArtifacts(
            delivery_mode="http_fallback",
            jar_uri=_jar_staging_local_uri(),
            s3_uri=None,
            staging_fetch_url=http_uri,
            http_download_uri=http_uri,
            uses_local_staging=True,
            local_staged_path=local_path,
            sha256=None,
        )
    return JarSubmitArtifacts(
        delivery_mode="http_fallback",
        jar_uri=http_uri,
        s3_uri=None,
        staging_fetch_url=None,
        http_download_uri=http_uri,
        uses_local_staging=False,
        local_staged_path=None,
        sha256=None,
    )


def prepare_jar_for_operator_submit(
    job: Any,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> JarSubmitArtifacts:
    """提交 JAR Operator 作业：物化 S3（若已配置）并解析 jarURI / staging URL。"""
    if artifact_s3_enabled(runtime_ctx):
        s3_ref = ensure_jar_in_profile_s3(job, runtime_ctx)
        return resolve_jar_submit_artifacts(
            int(job.id),
            runtime_ctx=runtime_ctx,
            jar_path=getattr(job, "jar_path", None),
            s3_ref=s3_ref,
        )
    nexus_url = (getattr(job, "jar_nexus_url", None) or "").strip()
    if nexus_url:
        content = fetch_jar_bytes_from_nexus(nexus_url)
        save_jar_bytes(
            int(job.id),
            content,
            runtime_ctx=None,
            original_filename=JAR_ARTIFACT_FILENAME,
        )
        job.jar_nexus_sha256 = _sha256_bytes(content)
    elif not jar_artifact_exists_local(int(job.id), jar_path=getattr(job, "jar_path", None)):
        raise RuntimeError("请配置 jar_nexus_url 或上传 JAR 文件")
    return resolve_jar_submit_artifacts(
        int(job.id),
        runtime_ctx=runtime_ctx,
        jar_path=getattr(job, "jar_path", None),
    )


def resolve_jar_uri_for_operator(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
) -> str:
    return resolve_jar_submit_artifacts(job_id, runtime_ctx=runtime_ctx, jar_path=jar_path).jar_uri


def _local_file_info(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {
            "path": str(path),
            "filename": path.name,
            "exists": False,
            "size_bytes": None,
            "last_modified": None,
        }
    st = path.stat()
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "path": str(path),
        "filename": path.name,
        "exists": True,
        "size_bytes": int(st.st_size),
        "last_modified": mtime,
    }


def jar_artifact_inventory(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
    include_cluster_jobs: bool = True,
) -> Dict[str, Any]:
    """本作业 JAR 制品清单（S3 + 本地镜像 + 提交用 URI）。"""
    _ = include_cluster_jobs
    storage_fn = resolve_jar_storage_filename(job_id, jar_path=jar_path)
    local_path = resolve_artifact_file_path(job_id, jar_path=jar_path)
    http_base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip().rstrip("/") or None
    s3_uri = build_s3_artifact_uri(job_id, storage_fn, runtime_ctx=runtime_ctx)
    s3_enabled = artifact_s3_enabled(runtime_ctx)
    fv = getattr(runtime_ctx, "flink_version", None) if runtime_ctx else None
    delivery = resolve_jar_delivery_mode(fv) if s3_enabled else "http_fallback"

    out: Dict[str, Any] = {
        "job_id": int(job_id),
        "storage_mode": "s3" if s3_enabled else "local",
        "jar_delivery_mode": delivery,
        "operator_profile_id": getattr(runtime_ctx, "profile_id", None) if runtime_ctx else None,
        "operator_profile_name": getattr(runtime_ctx, "profile_name", None) if runtime_ctx else None,
        "original_filename": jar_storage_filename_from_jar_path(jar_path) or jar_path,
        "storage_filename": storage_fn,
        "jar_artifact_dir": str(Path(settings.JAR_ARTIFACT_DIR).expanduser().resolve()),
        "http_base": http_base,
        "s3_uri": s3_uri,
        "s3_prefix": artifact_s3_prefix(runtime_ctx),
        "local_artifact": _local_file_info(local_path),
        "operator_jar_uri": None,
        "operator_jar_uri_error": None,
        "artifact_ready": False,
    }
    try:
        out["artifact_ready"] = jar_artifact_exists(job_id, runtime_ctx=runtime_ctx, jar_path=jar_path)
    except Exception as ex:
        logger.warning("探测制品就绪状态失败 job=%s: %s", job_id, ex)
    try:
        if s3_enabled and out["artifact_ready"]:
            submit = resolve_jar_submit_artifacts(
                job_id,
                runtime_ctx=runtime_ctx,
                jar_path=jar_path,
                s3_ref=S3ArtifactRef(uri=s3_uri or "", sha256="", skipped_upload=True),
            )
        else:
            submit = resolve_jar_submit_artifacts(job_id, runtime_ctx=runtime_ctx, jar_path=jar_path)
        out["http_download_uri"] = submit.http_download_uri
        out["staging_fetch_url"] = submit.staging_fetch_url
        out["uses_local_staging"] = submit.uses_local_staging
        out["local_staged_path"] = submit.local_staged_path
        out["operator_jar_uri"] = submit.jar_uri
        out["jar_delivery_mode"] = submit.delivery_mode
    except Exception as ex:
        out["operator_jar_uri_error"] = str(ex)
    if s3_enabled and not s3_uri:
        out["operator_jar_uri_error"] = s3_prefix_config_hint(runtime_ctx) or "S3 前缀无效"
    elif not s3_enabled and not http_base:
        out["operator_jar_uri_error"] = (
            "未配置 FLINK_OPERATOR_JAR_HTTP_BASE 或 Operator 集群 JAR 制品 S3 前缀。"
        )
    return out
