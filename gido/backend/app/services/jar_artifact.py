# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""JAR 制品：仅存 backend 本地 PVC；Flink Operator 经 HTTP 拉取（不用 S3 制品库）。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional
from urllib.parse import quote

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.operator_runtime import OperatorRuntimeContext

logger = logging.getLogger(__name__)

JAR_ARTIFACT_FILENAME = "artifact.jar"

_SAFE_JAR_BASENAME = re.compile(r"^[A-Za-z0-9._-]+\.jar$", re.IGNORECASE)


@dataclass(frozen=True)
class JarSaveResult:
    path: Path
    storage_filename: str = JAR_ARTIFACT_FILENAME


@dataclass(frozen=True)
class JarSubmitArtifacts:
    jar_uri: str
    http_download_uri: str
    uses_local_staging: bool = False
    local_staged_path: Optional[str] = None


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


def _jar_staging_local_uri() -> str:
    mount = (settings.FLINK_OPERATOR_JAR_STAGING_MOUNT or "/opt/flink/usrlib/gido-artifacts").strip()
    return f"local://{mount.rstrip('/')}/job.jar"


def resolve_jar_submit_artifacts(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
) -> JarSubmitArtifacts:
    _ = jar_path
    http_uri = build_jar_http_uri_for_operator(job_id)
    fv = (
        getattr(runtime_ctx, "flink_version", None)
        if runtime_ctx is not None
        else getattr(settings, "FLINK_OPERATOR_FLINK_VERSION", None)
    )
    if flink_jar_needs_local_staging(fv):
        local_path = _jar_staging_local_uri().removeprefix("local://")
        return JarSubmitArtifacts(
            jar_uri=_jar_staging_local_uri(),
            http_download_uri=http_uri,
            uses_local_staging=True,
            local_staged_path=local_path,
        )
    return JarSubmitArtifacts(jar_uri=http_uri, http_download_uri=http_uri)


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
    """写入 backend 本地制品库（JAR_ARTIFACT_DIR / PVC）。"""
    _ = runtime_ctx
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
    return JarSaveResult(path=path, storage_filename=storage_fn)


def jar_artifact_exists(
    job_id: int,
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
    *,
    jar_path: Optional[str] = None,
) -> bool:
    _ = runtime_ctx
    p = resolve_artifact_file_path(job_id, jar_path=jar_path)
    return p.is_file() and p.stat().st_size > 0


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
    """Flink Operator job.jarURI：Flink Pod 须能访问该 HTTP URL。"""
    base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "未配置 FLINK_OPERATOR_JAR_HTTP_BASE（Flink 集群拉取 JAR 的 GIDO API 基址，"
            "K8s 示例：http://gido-backend.gido.svc.cluster.local:8001）。"
        )
    token = quote(resolved_artifact_download_token(), safe="")
    return f"{base}/api/streaming/jobs/{int(job_id)}/artifact.jar?token={token}"


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
    """本作业 JAR 制品清单（backend 本地 PVC + 提交用 HTTP URI）。"""
    _ = include_cluster_jobs
    storage_fn = resolve_jar_storage_filename(job_id, jar_path=jar_path)
    local_path = resolve_artifact_file_path(job_id, jar_path=jar_path)
    http_base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip().rstrip("/") or None

    out: Dict[str, Any] = {
        "job_id": int(job_id),
        "storage_mode": "local",
        "operator_profile_id": getattr(runtime_ctx, "profile_id", None) if runtime_ctx else None,
        "operator_profile_name": getattr(runtime_ctx, "profile_name", None) if runtime_ctx else None,
        "original_filename": jar_storage_filename_from_jar_path(jar_path) or jar_path,
        "storage_filename": storage_fn,
        "jar_artifact_dir": str(Path(settings.JAR_ARTIFACT_DIR).expanduser().resolve()),
        "http_base": http_base,
        "local_artifact": _local_file_info(local_path),
        "operator_jar_uri": None,
        "operator_jar_uri_error": None,
        "artifact_ready": False,
    }
    if not http_base:
        out["operator_jar_uri_error"] = (
            "未配置 FLINK_OPERATOR_JAR_HTTP_BASE，Flink Operator 无法 HTTP 拉取 JAR。"
        )
    try:
        out["artifact_ready"] = jar_artifact_exists(job_id, runtime_ctx=runtime_ctx, jar_path=jar_path)
    except Exception as ex:
        logger.warning("探测制品就绪状态失败 job=%s: %s", job_id, ex)
    try:
        submit = resolve_jar_submit_artifacts(job_id, runtime_ctx=runtime_ctx, jar_path=jar_path)
        out["http_download_uri"] = submit.http_download_uri
        out["uses_local_staging"] = submit.uses_local_staging
        out["local_staged_path"] = submit.local_staged_path
        out["operator_jar_uri"] = submit.jar_uri
    except Exception as ex:
        out["operator_jar_uri_error"] = str(ex)

    return out
