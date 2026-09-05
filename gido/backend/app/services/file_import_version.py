# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""文件导入版本：不可变快照、指纹、兼容迁移与 schema diff。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.workspace import FileImportVersion, SyncTask
from app.services.file_import_exec import normalize_columns


OPERATION_MODES = ("create", "append", "replace")
QUALITY_MODES = ("strict", "lenient")


def schema_fingerprint(columns: Sequence[Dict[str, Any]]) -> str:
    cols = normalize_columns(columns)
    payload = [
        {
            "name": c.get("name"),
            "type": (c.get("type") or "").lower(),
            "nullable": bool(c.get("nullable", True)),
        }
        for c in cols
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def operation_mode_from_if_exists(if_exists: Optional[str]) -> str:
    v = (if_exists or "fail").strip().lower()
    if v in ("fail", "create"):
        return "create"
    if v == "append":
        return "append"
    if v == "replace":
        return "replace"
    raise ValueError("operation_mode / if_exists 仅支持 create(fail) / append / replace")


def if_exists_from_operation_mode(mode: str) -> str:
    m = (mode or "create").strip().lower()
    if m == "create":
        return "fail"
    if m in ("append", "replace"):
        return m
    raise ValueError("operation_mode 无效")


def column_schema_diff(
    expected: Sequence[Dict[str, Any]],
    actual: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """比较期望字段与目标表现有字段；返回兼容性与差异明细。"""
    exp = {str(c["name"]).lower(): c for c in normalize_columns(expected)}
    act = {str(c.get("name") or c.get("column_name") or "").lower(): c for c in (actual or [])}
    act = {k: v for k, v in act.items() if k}
    missing = sorted(set(exp) - set(act))
    extra = sorted(set(act) - set(exp))
    type_mismatch: List[Dict[str, Any]] = []
    for name, ec in exp.items():
        if name not in act:
            continue
        et = str(ec.get("type") or "").lower()
        at = str(act[name].get("type") or act[name].get("data_type") or "").lower()
        if et and at and et.split("(")[0] != at.split("(")[0]:
            type_mismatch.append({"name": name, "expected": et, "actual": at})
    compatible = not missing and not type_mismatch
    return {
        "compatible": compatible,
        "missing_in_target": missing,
        "extra_in_target": extra,
        "type_mismatch": type_mismatch,
        "expected_fingerprint": schema_fingerprint(expected),
        "actual_count": len(act),
        "expected_count": len(exp),
    }


def version_to_dict(v: FileImportVersion) -> Dict[str, Any]:
    return {
        "id": v.id,
        "sync_task_id": v.sync_task_id,
        "workspace_id": v.workspace_id,
        "file_id": v.file_id,
        "content_sha256": v.content_sha256,
        "original_filename": v.original_filename,
        "format": v.format,
        "encoding": v.encoding,
        "delimiter": v.delimiter,
        "has_header": v.has_header,
        "sheet_name": v.sheet_name,
        "columns": v.columns or [],
        "schema_fingerprint": v.schema_fingerprint,
        "operation_mode": v.operation_mode,
        "quality_mode": v.quality_mode,
        "status": v.status,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "activated_at": v.activated_at.isoformat() if v.activated_at else None,
    }


def create_version(
    db: Session,
    *,
    task: SyncTask,
    file_id: str,
    columns: Sequence[Dict[str, Any]],
    operation_mode: str,
    meta: Dict[str, Any],
    encoding: Optional[str] = None,
    delimiter: Optional[str] = None,
    has_header: bool = True,
    sheet_name: Optional[str] = None,
    quality_mode: str = "strict",
    content_sha256: Optional[str] = None,
    created_by: Optional[int] = None,
    activate: bool = False,
) -> FileImportVersion:
    mode = (operation_mode or "create").strip().lower()
    if mode not in OPERATION_MODES:
        raise ValueError("operation_mode 仅支持 create / append / replace")
    qm = (quality_mode or "strict").strip().lower()
    if qm not in QUALITY_MODES:
        raise ValueError("quality_mode 仅支持 strict / lenient")
    cols = normalize_columns(columns)
    if not cols:
        raise ValueError("columns 不能为空")
    ver = FileImportVersion(
        sync_task_id=task.id,
        workspace_id=task.workspace_id,
        file_id=file_id,
        content_sha256=content_sha256 or meta.get("content_sha256"),
        original_filename=str(meta.get("original_filename") or ""),
        format=str(meta.get("format") or "csv"),
        encoding=encoding,
        delimiter=delimiter,
        has_header=bool(has_header),
        sheet_name=sheet_name,
        columns=cols,
        schema_fingerprint=schema_fingerprint(cols),
        operation_mode=mode,
        quality_mode=qm,
        status="draft",
        created_by=created_by,
    )
    db.add(ver)
    db.flush()
    if activate:
        activate_version(db, task=task, version=ver)
    return ver


def activate_version(db: Session, *, task: SyncTask, version: FileImportVersion) -> FileImportVersion:
    if version.sync_task_id != task.id:
        raise ValueError("版本不属于该任务")
    others = (
        db.query(FileImportVersion)
        .filter(
            FileImportVersion.sync_task_id == task.id,
            FileImportVersion.id != version.id,
            FileImportVersion.status == "active",
        )
        .all()
    )
    for o in others:
        o.status = "superseded"
    version.status = "active"
    version.activated_at = datetime.utcnow()
    task.active_import_version_id = version.id
    # 保持 sync_config 与 active 版本对齐（兼容旧执行路径）
    cfg = dict(task.sync_config or {})
    cfg.update(
        {
            "source_type": "file",
            "file_id": version.file_id,
            "original_filename": version.original_filename,
            "format": version.format,
            "encoding": version.encoding,
            "delimiter": version.delimiter,
            "has_header": version.has_header,
            "sheet_name": version.sheet_name,
            "columns": version.columns,
            "if_exists": if_exists_from_operation_mode(version.operation_mode),
            "operation_mode": version.operation_mode,
            "quality_mode": version.quality_mode,
            "version_id": version.id,
            "schema_fingerprint": version.schema_fingerprint,
            "content_sha256": version.content_sha256,
        }
    )
    task.sync_config = cfg
    return version


def ensure_legacy_version(db: Session, task: SyncTask) -> Optional[FileImportVersion]:
    """旧任务仅有 sync_config.file_id 时，首次访问迁移为 active 版本。"""
    if task.sync_mode != "file_import":
        return None
    if task.active_import_version_id:
        v = db.query(FileImportVersion).filter(FileImportVersion.id == task.active_import_version_id).first()
        if v:
            return v
    existing = (
        db.query(FileImportVersion)
        .filter(FileImportVersion.sync_task_id == task.id, FileImportVersion.status == "active")
        .order_by(FileImportVersion.id.desc())
        .first()
    )
    if existing:
        task.active_import_version_id = existing.id
        return existing
    cfg = dict(task.sync_config or {})
    file_id = str(cfg.get("file_id") or "").strip()
    cols = cfg.get("columns") or []
    if not file_id or not cols:
        return None
    try:
        mode = operation_mode_from_if_exists(str(cfg.get("if_exists") or "fail"))
    except ValueError:
        mode = "append"
    ver = create_version(
        db,
        task=task,
        file_id=file_id,
        columns=cols,
        operation_mode=mode,
        meta={
            "original_filename": cfg.get("original_filename") or task.src_table,
            "format": cfg.get("format") or "csv",
            "content_sha256": cfg.get("content_sha256"),
        },
        encoding=cfg.get("encoding"),
        delimiter=cfg.get("delimiter"),
        has_header=bool(cfg.get("has_header", True)),
        sheet_name=cfg.get("sheet_name"),
        quality_mode=str(cfg.get("quality_mode") or "strict"),
        content_sha256=cfg.get("content_sha256"),
        created_by=task.created_by,
        activate=True,
    )
    return ver


def list_versions(db: Session, task_id: int) -> List[FileImportVersion]:
    return (
        db.query(FileImportVersion)
        .filter(FileImportVersion.sync_task_id == task_id)
        .order_by(FileImportVersion.id.desc())
        .all()
    )


def config_snapshot_from_version(version: FileImportVersion) -> Dict[str, Any]:
    return {
        "version_id": version.id,
        "file_id": version.file_id,
        "content_sha256": version.content_sha256,
        "columns": version.columns,
        "schema_fingerprint": version.schema_fingerprint,
        "operation_mode": version.operation_mode,
        "quality_mode": version.quality_mode,
        "encoding": version.encoding,
        "delimiter": version.delimiter,
        "has_header": version.has_header,
        "sheet_name": version.sheet_name,
        "format": version.format,
        "original_filename": version.original_filename,
    }
