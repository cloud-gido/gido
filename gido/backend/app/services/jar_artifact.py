# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""JAR 制品本地存储 + 可选 S3 持久化（按 Operator 集群 Profile 前缀）；Operator 通过 HTTP 或 s3:// jarURI 拉取。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import quote

from app.core.config import settings
from app.services.artifact_s3 import (
    JAR_ARTIFACT_FILENAME,
    artifact_exists_in_s3,
    artifact_s3_enabled,
    build_s3_artifact_uri,
    upload_artifact_bytes,
)

if TYPE_CHECKING:
    from app.services.operator_runtime import OperatorRuntimeContext

logger = logging.getLogger(__name__)

_SAFE_JAR_BASENAME = re.compile(r"^[A-Za-z0-9._-]+\.jar$", re.IGNORECASE)


@dataclass(frozen=True)
class JarSaveResult:
    path: Path
    storage_filename: str = JAR_ARTIFACT_FILENAME
    s3_uri: Optional[str] = None
    s3_sync_error: Optional[str] = None
    s3_prefix: Optional[str] = None

    @property
    def s3_synced(self) -> bool:
        return self.s3_uri is not None


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


def save_jar_bytes(
    job_id: int,
    content: bytes,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    original_filename: Optional[str] = None,
) -> JarSaveResult:
    """写入本地制品库；若该 Operator 集群（或平台）配置了 S3 前缀则尝试同步。"""
    from app.services.artifact_s3 import artifact_s3_prefix

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
    prefix = artifact_s3_prefix(runtime_ctx)
    s3_uri: Optional[str] = None
    s3_err: Optional[str] = None
    if prefix:
        try:
            s3_uri = upload_artifact_bytes(
                job_id,
                storage_fn,
                content,
                content_type="application/java-archive",
                runtime_ctx=runtime_ctx,
            )
        except Exception as ex:
            s3_err = str(ex)
            logger.error("JAR 上传 S3 失败 job=%s prefix=%s: %s", job_id, prefix, ex)
    return JarSaveResult(
        path=path,
        storage_filename=storage_fn,
        s3_uri=s3_uri,
        s3_sync_error=s3_err,
        s3_prefix=prefix,
    )


def jar_artifact_exists(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
) -> bool:
    p = resolve_artifact_file_path(job_id, jar_path=jar_path)
    if p.is_file() and p.stat().st_size > 0:
        return True
    if artifact_s3_enabled(runtime_ctx):
        storage_fn = resolve_jar_storage_filename(job_id, jar_path=jar_path)
        if artifact_exists_in_s3(job_id, storage_fn, runtime_ctx=runtime_ctx):
            return True
        if storage_fn != JAR_ARTIFACT_FILENAME and artifact_exists_in_s3(
            job_id, JAR_ARTIFACT_FILENAME, runtime_ctx=runtime_ctx
        ):
            return True
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
    """Flink Operator job.jarURI：集群内 Pod 须能访问该 URL（Docker 默认可解析 host.docker.internal）。"""
    base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "未配置 FLINK_OPERATOR_JAR_HTTP_BASE（Flink 集群拉取 JAR 的 GIDO API 基址，"
            "Docker 示例：http://host.docker.internal:8001）。"
        )
    token = quote(resolved_artifact_download_token(), safe="")
    return f"{base}/api/streaming/jobs/{int(job_id)}/artifact.jar?token={token}"


def build_jar_s3_uri_for_operator(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
) -> Optional[str]:
    storage_fn = resolve_jar_storage_filename(job_id, jar_path=jar_path)
    return build_s3_artifact_uri(job_id, storage_fn, runtime_ctx=runtime_ctx)


def future_s3_uri_hint(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
) -> Optional[str]:
    return build_jar_s3_uri_for_operator(job_id, runtime_ctx=runtime_ctx, jar_path=jar_path)


def resolve_jar_uri_for_operator(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
) -> str:
    """S3 对象已存在时用 s3://；否则 HTTP。"""
    storage_fn = resolve_jar_storage_filename(job_id, jar_path=jar_path)
    s3_uri = build_jar_s3_uri_for_operator(job_id, runtime_ctx=runtime_ctx, jar_path=jar_path)
    if s3_uri:
        for fn in (storage_fn, JAR_ARTIFACT_FILENAME):
            if artifact_exists_in_s3(job_id, fn, runtime_ctx=runtime_ctx):
                uri = build_s3_artifact_uri(job_id, fn, runtime_ctx=runtime_ctx)
                if uri:
                    return uri
    return build_jar_http_uri_for_operator(job_id)


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
    """本作业制品库清单：本地 PVC + S3 作业目录 +（可选）集群根目录下 job 子目录。"""
    from app.services.artifact_s3 import (
        _parse_s3_prefix,
        artifact_s3_prefix,
        artifact_s3_prefix_source,
        artifact_s3_region_source,
        artifact_s3_endpoint_source,
        build_s3_artifact_uri,
        list_s3_job_folder_prefixes,
        list_s3_objects_under_key_prefix,
        s3_prefix_config_hint,
    )
    from app.services.s3_auth import _resolved_s3_endpoint, _resolved_s3_region

    storage_fn = resolve_jar_storage_filename(job_id, jar_path=jar_path)
    prefix = artifact_s3_prefix(runtime_ctx)
    prefix_error = s3_prefix_config_hint(runtime_ctx) if not prefix else None

    local_path = resolve_artifact_file_path(job_id, jar_path=jar_path)
    out: Dict[str, Any] = {
        "job_id": int(job_id),
        "operator_profile_id": getattr(runtime_ctx, "profile_id", None) if runtime_ctx else None,
        "operator_profile_name": getattr(runtime_ctx, "profile_name", None) if runtime_ctx else None,
        "original_filename": jar_storage_filename_from_jar_path(jar_path) or jar_path,
        "storage_filename": storage_fn,
        "s3_prefix": prefix,
        "s3_prefix_source": artifact_s3_prefix_source(runtime_ctx) if not prefix_error else None,
        "s3_region": _resolved_s3_region(runtime_ctx),
        "s3_region_source": artifact_s3_region_source(runtime_ctx),
        "s3_endpoint_url": _resolved_s3_endpoint(runtime_ctx),
        "s3_endpoint_source": artifact_s3_endpoint_source(runtime_ctx),
        "local_artifact": _local_file_info(local_path),
        "job_s3_prefix": None,
        "job_s3_objects": [],
        "cluster_job_folders": [],
        "s3_list_error": prefix_error,
        "expected_jar_uri": None,
        "operator_jar_uri": None,
        "artifact_ready": False,
    }
    if prefix and not prefix_error:
        try:
            out["expected_jar_uri"] = build_s3_artifact_uri(
                job_id, storage_fn, runtime_ctx=runtime_ctx
            )
        except Exception as ex:
            out["s3_list_error"] = str(ex)
    try:
        out["artifact_ready"] = jar_artifact_exists(job_id, runtime_ctx=runtime_ctx, jar_path=jar_path)
    except Exception as ex:
        logger.warning("探测制品就绪状态失败 job=%s: %s", job_id, ex)
    try:
        out["operator_jar_uri"] = resolve_jar_uri_for_operator(
            job_id, runtime_ctx=runtime_ctx, jar_path=jar_path
        )
    except Exception as ex:
        out["operator_jar_uri"] = None
        out["operator_jar_uri_error"] = str(ex)

    if not prefix or prefix_error:
        return out

    try:
        bucket, key_prefix = _parse_s3_prefix(prefix)
    except ValueError as ex:
        out["s3_list_error"] = str(ex)
        return out

    job_key_prefix = "/".join(p for p in (key_prefix, str(int(job_id))) if p)
    out["job_s3_prefix"] = f"s3://{bucket}/{job_key_prefix}/"

    job_objects, job_err = list_s3_objects_under_key_prefix(
        bucket, job_key_prefix, runtime_ctx=runtime_ctx
    )
    out["job_s3_objects"] = job_objects
    if job_err:
        out["s3_list_error"] = job_err

    if include_cluster_jobs:
        folders, folder_err = list_s3_job_folder_prefixes(bucket, key_prefix, runtime_ctx=runtime_ctx)
        out["cluster_job_folders"] = folders
        if folder_err and not out["s3_list_error"]:
            out["s3_list_error"] = folder_err

    return out
