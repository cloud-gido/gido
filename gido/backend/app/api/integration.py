# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import DataSource, SyncRecord, SyncTask, User
from app.services.integration_runtime import SUPPORTED_DS_TYPES, list_columns, list_tables, test_connection
from app.services.integration_sync import start_sync_async, validate_task_config
from app.services.rbac import assert_workspace_data_capability, require_sync_task, require_datasource_row

router = APIRouter(prefix="/integration", tags=["数据集成"])

SYNC_MODES = ("full", "incremental", "cdc")


class FieldMapping(BaseModel):
    src: str
    dst: str


class CdcConfigIn(BaseModel):
    poll_interval_sec: Optional[int] = Field(default=10, ge=3, le=3600)


class SyncConfigIn(BaseModel):
    field_mappings: Optional[List[FieldMapping]] = None
    where_clause: Optional[str] = None
    incremental_column: Optional[str] = None
    incremental_start: Optional[str] = None
    last_value: Optional[str] = None
    batch_size: Optional[int] = Field(default=2000, ge=100, le=10000)
    pre_sql: Optional[str] = None
    post_sql: Optional[str] = None
    cdc: Optional[CdcConfigIn] = None


class SyncTaskCreate(BaseModel):
    workspace_id: int
    name: str
    description: Optional[str] = None
    src_datasource_id: int
    dst_datasource_id: int
    src_table: str
    dst_table: str
    sync_mode: str = "full"
    sync_config: Optional[SyncConfigIn] = None
    schedule_cron: Optional[str] = None
    is_active: bool = True


class SyncTaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    src_datasource_id: Optional[int] = None
    dst_datasource_id: Optional[int] = None
    src_table: Optional[str] = None
    dst_table: Optional[str] = None
    sync_mode: Optional[str] = None
    # 普通集成任务可用；file_import 禁止经此接口改 sync_config
    sync_config: Optional[Dict[str, Any]] = None
    schedule_cron: Optional[str] = None
    is_active: Optional[bool] = None


class SyncTaskOut(BaseModel):
    id: int
    workspace_id: int
    name: str
    description: Optional[str] = None
    src_datasource_id: int
    dst_datasource_id: int
    src_table: str
    dst_table: str
    sync_mode: str
    sync_config: Optional[Dict[str, Any]] = None
    schedule_cron: Optional[str] = None
    is_active: bool
    last_sync_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    active_import_version_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SyncRecordOut(BaseModel):
    id: int
    sync_task_id: int
    status: str
    trigger_type: Optional[str] = None
    rows_read: Optional[int] = 0
    rows_written: Optional[int] = 0
    error_msg: Optional[str] = None
    duration_ms: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    execution_key: Optional[str] = None
    retry_of: Optional[int] = None
    version_id: Optional[int] = None
    config_snapshot: Optional[Dict[str, Any]] = None
    phase: Optional[str] = None
    heartbeat_at: Optional[datetime] = None
    triggered_by: Optional[int] = None
    quality: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


def _sync_config_dump(cfg: Optional[SyncConfigIn]) -> Optional[Dict[str, Any]]:
    if cfg is None:
        return None
    d = cfg.model_dump(exclude_none=True)
    if cfg.field_mappings is not None:
        d["field_mappings"] = [m.model_dump() for m in cfg.field_mappings]
    if cfg.cdc is not None:
        d["cdc"] = cfg.cdc.model_dump(exclude_none=True)
    return d


def _validate_ds_pair(db: Session, workspace_id: int, src_id: int, dst_id: int) -> tuple:
    src_ds = db.query(DataSource).filter(DataSource.id == src_id).first()
    dst_ds = db.query(DataSource).filter(DataSource.id == dst_id).first()
    if not src_ds or src_ds.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="源数据源不存在或不属于该工作空间")
    if not dst_ds or dst_ds.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="目标数据源不存在或不属于该工作空间")
    return src_ds, dst_ds


def _assert_cron(cron: Optional[str]) -> None:
    if not cron or not str(cron).strip():
        return
    from croniter import croniter

    parts = str(cron).strip().split()
    if len(parts) != 5:
        raise HTTPException(status_code=400, detail="Cron 须为 5 段（分 时 日 月 周）")
    if not croniter.is_valid(str(cron).strip()):
        raise HTTPException(status_code=400, detail="Cron 表达式无效")


@router.get("/meta/supported-types")
def supported_types(_: User = Depends(get_current_user)):
    return {"types": sorted(SUPPORTED_DS_TYPES)}


@router.get("/datasources/{datasource_id}/schemas")
def datasource_schemas(
    datasource_id: int,
    keyword: str = Query("", max_length=128),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出库/schema，供 Studio/Probe SQL 补全跨库触发。"""
    from app.services.integration_runtime import list_schemas

    ds = require_datasource_row(db, current_user, datasource_id)
    try:
        return {"schemas": list_schemas(ds, keyword)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasources/{datasource_id}/tables")
def datasource_tables(
    datasource_id: int,
    keyword: str = Query("", max_length=128),
    catalog: Optional[str] = Query(None, max_length=128, description="MySQL/Doris 库名或 PG schema；默认数据源 database"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ds = require_datasource_row(db, current_user, datasource_id)
    try:
        return {"tables": list_tables(ds, keyword, catalog=catalog)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasource-columns")
def get_datasource_columns(
    datasource_id: int,
    table_name: str,
    catalog: Optional[str] = Query(None, max_length=128, description="MySQL/Doris 库名或 PG schema"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ds = require_datasource_row(db, current_user, datasource_id)
    try:
        return {"columns": list_columns(ds, table_name, catalog=catalog)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/datasources/{datasource_id}/test")
def test_datasource_connection(
    datasource_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ds = require_datasource_row(db, current_user, datasource_id)
    ok, msg = test_connection(ds)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}


@router.get("/tasks", response_model=List[SyncTaskOut])
def list_sync_tasks(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_READ)
    return (
        db.query(SyncTask)
        .filter(SyncTask.workspace_id == workspace_id)
        .order_by(SyncTask.id.desc())
        .all()
    )


@router.post("/tasks", response_model=SyncTaskOut)
def create_sync_task(task_in: SyncTaskCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, task_in.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE)
    if task_in.sync_mode == "file_import":
        raise HTTPException(status_code=400, detail="请使用「本地文件导入」接口创建任务")
    if task_in.sync_mode not in SYNC_MODES:
        raise HTTPException(status_code=400, detail="sync_mode 仅支持 full / incremental / cdc")
    if task_in.sync_mode == "cdc":
        sc = _sync_config_dump(task_in.sync_config) or {}
        if not sc.get("incremental_column"):
            raise HTTPException(status_code=400, detail="CDC 模式须配置 incremental_column")
    _assert_cron(task_in.schedule_cron)
    src_ds, dst_ds = _validate_ds_pair(
        db, task_in.workspace_id, task_in.src_datasource_id, task_in.dst_datasource_id
    )
    payload = task_in.model_dump()
    payload["sync_config"] = _sync_config_dump(task_in.sync_config)
    payload["created_by"] = current_user.id
    task = SyncTask(**payload)
    db.add(task)
    db.commit()
    db.refresh(task)
    from app.services.scheduler import reload_integration_schedules

    reload_integration_schedules()
    return task


@router.get("/tasks/{task_id}", response_model=SyncTaskOut)
def get_sync_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    assert_workspace_data_capability(db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_READ)
    return task


@router.put("/tasks/{task_id}", response_model=SyncTaskOut)
def update_sync_task(
    task_id: int,
    body: SyncTaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    assert_workspace_data_capability(db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE)
    data = body.model_dump(exclude_unset=True)
    if "sync_mode" in data and data["sync_mode"] not in SYNC_MODES:
        raise HTTPException(status_code=400, detail="sync_mode 仅支持 full / incremental / cdc")
    if "schedule_cron" in data:
        _assert_cron(data.get("schedule_cron"))
    if task.sync_mode == "file_import" and "sync_config" in data:
        raise HTTPException(
            status_code=400,
            detail="file_import 禁止经通用 update 修改 sync_config，请使用 /file-import/tasks/{id}/versions",
        )
    if "sync_config" in data:
        sc = body.sync_config
        data["sync_config"] = sc if sc is not None else task.sync_config
    ws = task.workspace_id
    # file_import 任务：仅允许改 name/description/is_active/dst_table 等元数据
    if task.sync_mode == "file_import":
        for k, v in data.items():
            if k in ("sync_mode", "sync_config"):
                continue
            setattr(task, k, v)
        task.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(task)
        return task
    src_id = data.get("src_datasource_id", task.src_datasource_id)
    dst_id = data.get("dst_datasource_id", task.dst_datasource_id)
    src_ds, dst_ds = _validate_ds_pair(db, ws, src_id, dst_id)
    for k, v in data.items():
        setattr(task, k, v)
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    from app.services.scheduler import reload_integration_schedules

    reload_integration_schedules()
    return task


@router.delete("/tasks/{task_id}")
def delete_sync_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    assert_workspace_data_capability(db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE)
    from app.services.integration_cdc import stop_task_cdc

    try:
        stop_task_cdc(task_id)
    except Exception:
        pass
    db.query(SyncRecord).filter(SyncRecord.sync_task_id == task_id).delete()
    db.delete(task)
    db.commit()
    from app.services.scheduler import reload_integration_schedules

    reload_integration_schedules()
    return {"message": "删除成功"}


@router.post("/tasks/{task_id}/toggle-active", response_model=SyncTaskOut)
def toggle_sync_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    assert_workspace_data_capability(db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE)
    task.is_active = not task.is_active
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    from app.services.scheduler import reload_integration_schedules

    reload_integration_schedules()
    return task


@router.post("/tasks/{task_id}/validate")
def validate_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = require_sync_task(db, current_user, task_id)
    if task.sync_mode == "file_import":
        dst_ds = db.query(DataSource).filter(DataSource.id == task.dst_datasource_id).first()
        if not dst_ds:
            raise HTTPException(status_code=400, detail="目标数据源不存在")
        ok_dst, msg_dst = test_connection(dst_ds)
        cfg = task.sync_config if isinstance(task.sync_config, dict) else {}
        warnings = []
        if not cfg.get("file_id"):
            warnings.append("缺少 file_id")
        if not task.dst_table:
            warnings.append("缺少目标表")
        return {
            "warnings": warnings,
            "src_connection": {"ok": True, "message": "本地文件源，无需连接"},
            "dst_connection": {"ok": ok_dst, "message": msg_dst},
        }
    src_ds = db.query(DataSource).filter(DataSource.id == task.src_datasource_id).first()
    dst_ds = db.query(DataSource).filter(DataSource.id == task.dst_datasource_id).first()
    if not src_ds or not dst_ds:
        raise HTTPException(status_code=400, detail="数据源不存在")
    warnings = validate_task_config(task, src_ds, dst_ds)
    ok_src, msg_src = test_connection(src_ds)
    ok_dst, msg_dst = test_connection(dst_ds)
    return {
        "warnings": warnings,
        "src_connection": {"ok": ok_src, "message": msg_src},
        "dst_connection": {"ok": ok_dst, "message": msg_dst},
    }


@router.post("/tasks/{task_id}/cdc/start")
def cdc_start(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = require_sync_task(db, current_user, task_id)
    assert_workspace_data_capability(db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_RUN)
    if task.sync_mode != "cdc":
        raise HTTPException(status_code=400, detail="仅 CDC 模式任务可启动实时同步")
    from app.services.integration_cdc import start_task_cdc

    start_task_cdc(task_id)
    return {"message": "CDC 已启动", "status": "running"}


@router.post("/tasks/{task_id}/cdc/stop")
def cdc_stop(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = require_sync_task(db, current_user, task_id)
    assert_workspace_data_capability(db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_RUN)
    from app.services.integration_cdc import stop_task_cdc

    stop_task_cdc(task_id)
    return {"message": "CDC 已停止"}


@router.get("/tasks/{task_id}/cdc/status")
def cdc_get_status(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = require_sync_task(db, current_user, task_id)
    from app.services.integration_cdc import cdc_status

    return cdc_status(task)


@router.post("/internal/tasks/{task_id}/run")
def internal_run_sync_task(
    task_id: int,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """供 Dolphin SHELL 节点回调；Bearer 须为 INTERNAL_TOKEN。"""
    token = (authorization or "").replace("Bearer ", "").strip()
    if not settings.INTERNAL_TOKEN or token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="无效的内部令牌")
    task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
    if not task or not task.is_active:
        raise HTTPException(status_code=404, detail="任务不存在或已停用")
    from app.services.integration_node import run_sync_for_node_blocking
    from app.models.workspace import TaskNode

    pseudo = TaskNode(
        id=0,
        workspace_id=task.workspace_id,
        name=task.name,
        node_type="SYNC",
        params={"sync_task_id": task_id},
    )
    logs, status, meta = run_sync_for_node_blocking(
        db, pseudo, trigger_type="dolphin", timeout_seconds=7200
    )
    if status != "success":
        raise HTTPException(status_code=500, detail="\n".join(logs))
    return {"status": status, "log": "\n".join(logs), "meta": meta}


class RunTaskIn(BaseModel):
    # 已废弃：file_import 请用版本 operation_mode；保留字段仅为兼容旧客户端
    if_exists: Optional[str] = None


@router.post("/tasks/{task_id}/run")
def run_sync_task(
    task_id: int,
    body: Optional[RunTaskIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    assert_workspace_data_capability(db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_RUN)
    if not task.is_active:
        raise HTTPException(status_code=400, detail="任务已停用")
    if task.sync_mode == "file_import":
        # 成功记录不可用通用 run 再跑；失败请走 retry；显式再导入请建新版本
        last = (
            db.query(SyncRecord)
            .filter(SyncRecord.sync_task_id == task_id)
            .order_by(SyncRecord.id.desc())
            .first()
        )
        if last and last.status == "success":
            raise HTTPException(
                status_code=400,
                detail="成功执行不可直接重跑；请创建新版本并选择 append/replace，或对失败记录使用 retry",
            )
        if last and last.status == "failed":
            raise HTTPException(
                status_code=400,
                detail="失败执行请调用 /file-import/records/{record_id}/retry 以复用 execution_key",
            )
        from app.services.file_import_version import ensure_legacy_version

        ver = ensure_legacy_version(db, task)
        db.commit()
        try:
            record = start_sync_async(
                task_id,
                trigger_type="manual",
                triggered_by=current_user.id,
                version_id=ver.id if ver else None,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {
            "record_id": record.id,
            "status": record.status,
            "message": "导入已排队，请在运行历史中查看进度",
            "execution_key": record.execution_key,
        }
    try:
        record = start_sync_async(task_id, trigger_type="manual", triggered_by=current_user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "record_id": record.id,
        "status": record.status,
        "message": "同步已排队执行，请在运行历史中查看进度",
    }


@router.get("/tasks/{task_id}/records", response_model=List[SyncRecordOut])
def list_sync_records(
    task_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_sync_task(db, current_user, task_id)
    return (
        db.query(SyncRecord)
        .filter(SyncRecord.sync_task_id == task_id)
        .order_by(SyncRecord.id.desc())
        .limit(limit)
        .all()
    )


@router.get("/tasks/{task_id}/records/{record_id}", response_model=SyncRecordOut)
def get_sync_record(
    task_id: int,
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_sync_task(db, current_user, task_id)
    record = (
        db.query(SyncRecord)
        .filter(SyncRecord.id == record_id, SyncRecord.sync_task_id == task_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return record


# ==================== 本地文件导入 ====================

class FileImportColumnIn(BaseModel):
    name: str
    type: str = "string"
    nullable: bool = True
    is_primary_key: bool = False


class FileImportPreviewIn(BaseModel):
    workspace_id: int
    file_id: str
    encoding: Optional[str] = None
    delimiter: Optional[str] = None
    has_header: bool = True
    sheet_name: Optional[str] = None


class FileImportDdlIn(BaseModel):
    datasource_id: int
    table_name: str
    columns: List[FileImportColumnIn]


class FileImportTaskCreate(BaseModel):
    workspace_id: int
    name: str
    description: Optional[str] = None
    dst_datasource_id: int
    dst_table: str
    file_id: str
    columns: List[FileImportColumnIn]
    encoding: Optional[str] = None
    delimiter: Optional[str] = None
    has_header: bool = True
    sheet_name: Optional[str] = None
    register_datamap: bool = True
    if_exists: Optional[str] = None  # 兼容旧客户端 fail|append|replace
    operation_mode: Optional[str] = None  # create|append|replace（优先）
    quality_mode: str = "strict"
    run_now: bool = True


class FileImportVersionCreate(BaseModel):
    file_id: str
    columns: List[FileImportColumnIn]
    operation_mode: str = "append"
    quality_mode: str = "strict"
    encoding: Optional[str] = None
    delimiter: Optional[str] = None
    has_header: bool = True
    sheet_name: Optional[str] = None
    activate: bool = True
    run_now: bool = False


class FileImportSchemaDiffIn(BaseModel):
    datasource_id: int
    table_name: str
    columns: List[FileImportColumnIn]


def _public_parse_result(parsed: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.file_import_store import file_import_storage_public

    storage = file_import_storage_public(meta)
    return {
        "file_id": meta.get("file_id"),
        "original_filename": meta.get("original_filename"),
        "format": parsed.get("format") or meta.get("format"),
        "size_bytes": meta.get("size_bytes") or parsed.get("size_bytes"),
        "encoding": parsed.get("encoding"),
        "delimiter": parsed.get("delimiter"),
        "has_header": parsed.get("has_header"),
        "sheet_name": parsed.get("sheet_name"),
        "sheet_names": parsed.get("sheet_names") or [],
        "columns": parsed.get("columns") or [],
        "preview_rows": parsed.get("preview_rows") or [],
        "row_count": parsed.get("row_count") or 0,
        "row_count_estimated": bool(parsed.get("row_count_estimated")),
        "max_bytes": settings.FILE_IMPORT_MAX_BYTES,
        "max_rows": settings.FILE_IMPORT_MAX_ROWS,
        "xlsx_max_bytes": settings.FILE_IMPORT_XLSX_MAX_BYTES,
        "chunk_bytes": settings.FILE_IMPORT_CHUNK_BYTES,
        **storage,
    }


class FileImportUploadInitIn(BaseModel):
    workspace_id: int
    filename: str
    size_bytes: int
    total_chunks: int
    client_key: Optional[str] = None
    chunk_bytes: Optional[int] = None
    force_new: bool = False


@router.post("/file-import/upload")
async def file_import_upload(
    workspace_id: int = Form(...),
    file: UploadFile = File(...),
    encoding: Optional[str] = Form(None),
    delimiter: Optional[str] = Form(None),
    has_header: bool = Form(True),
    sheet_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(
        db, current_user, workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    try:
        from app.services.file_import_store import save_upload_stream, resolve_data_path
        from app.services.file_import_parse import parse_file_path

        async def _chunks():
            while True:
                chunk = await file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                yield chunk

        meta = await save_upload_stream(
            workspace_id=workspace_id,
            user_id=current_user.id,
            filename=file.filename or "upload.csv",
            chunks=_chunks(),
        )
        path = resolve_data_path(meta)
        parsed = parse_file_path(
            path,
            str(meta["format"]),
            encoding=encoding,
            delimiter=delimiter,
            has_header=has_header,
            sheet_name=sheet_name,
            max_rows=int(settings.FILE_IMPORT_MAX_ROWS),
            preview_rows=int(settings.FILE_IMPORT_PREVIEW_ROWS),
            infer_rows=int(settings.FILE_IMPORT_INFER_ROWS),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析失败: {e}")
    return _public_parse_result(parsed, meta)


@router.post("/file-import/upload-init")
def file_import_upload_init(
    body: FileImportUploadInitIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """大文件分片上传：初始化会话（避免单次 multipart 触发 HTTP/2 ping 失败）。"""
    assert_workspace_data_capability(
        db, current_user, body.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    try:
        from app.services.file_import_store import init_chunked_upload

        return init_chunked_upload(
            workspace_id=body.workspace_id,
            user_id=current_user.id,
            filename=body.filename,
            size_bytes=body.size_bytes,
            total_chunks=body.total_chunks,
            client_key=body.client_key,
            chunk_bytes=body.chunk_bytes,
            force_new=bool(body.force_new),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/file-import/upload-chunk")
async def file_import_upload_chunk(
    workspace_id: int = Form(...),
    file_id: str = Form(...),
    chunk_index: int = Form(...),
    file: UploadFile = File(...),
    chunk_sha256: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(
        db, current_user, workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    try:
        from app.services.file_import_store import save_upload_chunk

        raw = await file.read()
        return save_upload_chunk(
            workspace_id=workspace_id,
            file_id=file_id,
            chunk_index=chunk_index,
            content=raw,
            expected_sha256=chunk_sha256,
            user_id=current_user.id,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/file-import/upload-status")
def file_import_upload_status(
    workspace_id: int,
    file_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(
        db, current_user, workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    try:
        from app.services.file_import_store import get_upload_status

        return get_upload_status(workspace_id, file_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/file-import/upload-abort")
def file_import_upload_abort(
    workspace_id: int = Form(...),
    file_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(
        db, current_user, workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    try:
        from app.services.file_import_store import abort_chunked_upload

        return abort_chunked_upload(workspace_id=workspace_id, file_id=file_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/file-import/upload-complete")
def file_import_upload_complete(
    workspace_id: int = Form(...),
    file_id: str = Form(...),
    encoding: Optional[str] = Form(None),
    delimiter: Optional[str] = Form(None),
    has_header: bool = Form(True),
    sheet_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(
        db, current_user, workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    try:
        from app.services.file_import_store import finalize_chunked_upload, resolve_data_path
        from app.services.file_import_parse import parse_file_path

        meta = finalize_chunked_upload(workspace_id=workspace_id, file_id=file_id, user_id=current_user.id)
        path = resolve_data_path(meta)
        parsed = parse_file_path(
            path,
            str(meta.get("format") or "csv"),
            encoding=encoding,
            delimiter=delimiter,
            has_header=has_header,
            sheet_name=sheet_name,
            max_rows=int(settings.FILE_IMPORT_MAX_ROWS),
            preview_rows=int(settings.FILE_IMPORT_PREVIEW_ROWS),
            infer_rows=int(settings.FILE_IMPORT_INFER_ROWS),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析失败: {e}")
    return _public_parse_result(parsed, meta)


@router.post("/file-import/preview")
def file_import_preview(
    body: FileImportPreviewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(
        db, current_user, body.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    try:
        from app.services.file_import_store import load_meta, resolve_data_path
        from app.services.file_import_parse import parse_file_path

        meta = load_meta(body.workspace_id, body.file_id)
        path = resolve_data_path(meta)
        parsed = parse_file_path(
            path,
            str(meta.get("format") or "csv"),
            encoding=body.encoding,
            delimiter=body.delimiter,
            has_header=body.has_header,
            sheet_name=body.sheet_name,
            max_rows=int(settings.FILE_IMPORT_MAX_ROWS),
            preview_rows=int(settings.FILE_IMPORT_PREVIEW_ROWS),
            infer_rows=int(settings.FILE_IMPORT_INFER_ROWS),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析失败: {e}")
    return _public_parse_result(parsed, meta)


@router.post("/file-import/preview-ddl")
def file_import_preview_ddl(
    body: FileImportDdlIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ds = require_datasource_row(db, current_user, body.datasource_id)
    assert_workspace_data_capability(
        db, current_user, ds.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    try:
        from app.services.file_import_exec import build_create_table_ddl, table_exists

        cols = [c.model_dump() for c in body.columns]
        ddl = build_create_table_ddl(ds, body.table_name, cols)
        exists = table_exists(ds, body.table_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ddl": ddl, "table_exists": exists, "ds_type": (ds.ds_type or "").lower()}


@router.post("/file-import/tasks")
def create_file_import_task(
    body: FileImportTaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(
        db, current_user, body.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    from app.services.file_import_version import operation_mode_from_if_exists, create_version, version_to_dict

    try:
        op_mode = (body.operation_mode or "").strip().lower() or operation_mode_from_if_exists(body.if_exists or "fail")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ds = require_datasource_row(db, current_user, body.dst_datasource_id)
    if ds.workspace_id != body.workspace_id:
        raise HTTPException(status_code=400, detail="目标数据源不属于该工作空间")
    ds_type = (ds.ds_type or "").lower()
    if ds_type not in ("mysql", "doris"):
        raise HTTPException(status_code=400, detail="本地文件导入暂仅支持 MySQL / Doris 目标")

    try:
        from app.services.file_import_store import load_meta
        from app.services.file_import_exec import normalize_columns, validate_table_name, table_exists
        from app.services.file_import_version import column_schema_diff
        from app.services.integration_runtime import list_columns

        meta = load_meta(body.workspace_id, body.file_id)
        if meta.get("user_id") is not None and int(meta["user_id"]) != int(current_user.id):
            # 允许同空间同事复用 ready 文件；仅警告级——生产可收紧
            pass
        table_name = validate_table_name(body.dst_table)
        cols = normalize_columns([c.model_dump() for c in body.columns])
        exists = table_exists(ds, table_name)
        if op_mode == "create" and exists:
            raise ValueError(f"目标表已存在: {table_name}")
        if op_mode == "append" and exists:
            diff = column_schema_diff(cols, list_columns(ds, table_name) or [])
            if not diff.get("compatible"):
                raise ValueError(f"append 结构不兼容: {diff}")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    sync_config = {
        "source_type": "file",
        "file_id": body.file_id,
        "original_filename": meta.get("original_filename"),
        "format": meta.get("format"),
        "encoding": body.encoding,
        "delimiter": body.delimiter,
        "has_header": body.has_header,
        "sheet_name": body.sheet_name,
        "columns": cols,
        "register_datamap": body.register_datamap,
        "if_exists": "fail" if op_mode == "create" else op_mode,
        "operation_mode": op_mode,
        "quality_mode": body.quality_mode,
        "content_sha256": meta.get("content_sha256"),
        "batch_size": 2000,
    }
    task = SyncTask(
        workspace_id=body.workspace_id,
        name=body.name.strip(),
        description=body.description,
        src_datasource_id=body.dst_datasource_id,
        dst_datasource_id=body.dst_datasource_id,
        src_table=str(meta.get("original_filename") or "local_file"),
        dst_table=table_name,
        sync_mode="file_import",
        sync_config=sync_config,
        schedule_cron=None,
        is_active=True,
        created_by=current_user.id,
    )
    db.add(task)
    db.flush()
    ver = create_version(
        db,
        task=task,
        file_id=body.file_id,
        columns=cols,
        operation_mode=op_mode,
        meta=meta,
        encoding=body.encoding,
        delimiter=body.delimiter,
        has_header=body.has_header,
        sheet_name=body.sheet_name,
        quality_mode=body.quality_mode,
        content_sha256=meta.get("content_sha256"),
        created_by=current_user.id,
        activate=True,
    )
    db.commit()
    db.refresh(task)

    out: Dict[str, Any] = {
        "task": SyncTaskOut.model_validate(task).model_dump(),
        "version": version_to_dict(ver),
        "record_id": None,
        "message": "导入任务已创建",
    }
    if body.run_now:
        assert_workspace_data_capability(
            db, current_user, body.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_RUN
        )
        try:
            record = start_sync_async(
                task.id,
                trigger_type="manual",
                triggered_by=current_user.id,
                version_id=ver.id,
            )
            out["record_id"] = record.id
            out["execution_key"] = record.execution_key
            out["message"] = "导入已排队执行，请在运行历史中查看进度"
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
    return out


@router.get("/file-import/tasks/{task_id}/versions")
def list_file_import_versions(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = require_sync_task(db, current_user, task_id)
    if task.sync_mode != "file_import":
        raise HTTPException(status_code=400, detail="非文件导入任务")
    from app.services.file_import_version import ensure_legacy_version, list_versions, version_to_dict

    ensure_legacy_version(db, task)
    db.commit()
    return [version_to_dict(v) for v in list_versions(db, task_id)]


@router.post("/file-import/tasks/{task_id}/versions")
def create_file_import_version(
    task_id: int,
    body: FileImportVersionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = require_sync_task(db, current_user, task_id)
    assert_workspace_data_capability(
        db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    if task.sync_mode != "file_import":
        raise HTTPException(status_code=400, detail="非文件导入任务")
    from app.services.file_import_store import load_meta
    from app.services.file_import_version import create_version, version_to_dict, column_schema_diff
    from app.services.file_import_exec import normalize_columns, table_exists
    from app.services.integration_runtime import list_columns

    try:
        meta = load_meta(task.workspace_id, body.file_id)
        cols = normalize_columns([c.model_dump() for c in body.columns])
        ds = require_datasource_row(db, current_user, task.dst_datasource_id)
        exists = table_exists(ds, task.dst_table)
        mode = body.operation_mode.strip().lower()
        if mode == "create" and exists:
            raise ValueError("目标表已存在，请使用 append 或 replace")
        if mode == "append" and exists:
            diff = column_schema_diff(cols, list_columns(ds, task.dst_table) or [])
            if not diff.get("compatible"):
                raise ValueError(f"append 结构不兼容: {diff}")
        ver = create_version(
            db,
            task=task,
            file_id=body.file_id,
            columns=cols,
            operation_mode=mode,
            meta=meta,
            encoding=body.encoding,
            delimiter=body.delimiter,
            has_header=body.has_header,
            sheet_name=body.sheet_name,
            quality_mode=body.quality_mode,
            content_sha256=meta.get("content_sha256"),
            created_by=current_user.id,
            activate=bool(body.activate),
        )
        db.commit()
        db.refresh(ver)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    out: Dict[str, Any] = {"version": version_to_dict(ver), "record_id": None}
    if body.run_now:
        assert_workspace_data_capability(
            db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_RUN
        )
        try:
            record = start_sync_async(
                task.id,
                trigger_type="manual",
                triggered_by=current_user.id,
                version_id=ver.id,
            )
            out["record_id"] = record.id
            out["execution_key"] = record.execution_key
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
    return out


@router.post("/file-import/schema-diff")
def file_import_schema_diff(
    body: FileImportSchemaDiffIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ds = require_datasource_row(db, current_user, body.datasource_id)
    assert_workspace_data_capability(
        db, current_user, ds.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_WRITE
    )
    from app.services.file_import_version import column_schema_diff
    from app.services.file_import_exec import table_exists, normalize_columns
    from app.services.integration_runtime import list_columns

    cols = normalize_columns([c.model_dump() for c in body.columns])
    exists = table_exists(ds, body.table_name)
    actual = list_columns(ds, body.table_name) if exists else []
    diff = column_schema_diff(cols, actual)
    return {"table_exists": exists, "diff": diff}


@router.post("/file-import/records/{record_id}/retry")
def retry_file_import_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rec = db.query(SyncRecord).filter(SyncRecord.id == record_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    task = require_sync_task(db, current_user, rec.sync_task_id)
    assert_workspace_data_capability(
        db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_RUN
    )
    if task.sync_mode != "file_import":
        raise HTTPException(status_code=400, detail="仅文件导入支持幂等 retry")
    if rec.status != "failed":
        raise HTTPException(status_code=400, detail="仅失败记录可 retry")
    if not rec.execution_key:
        raise HTTPException(status_code=400, detail="记录缺少 execution_key，无法幂等重试")
    try:
        new_rec = start_sync_async(
            task.id,
            trigger_type="retry",
            triggered_by=current_user.id,
            execution_key=rec.execution_key,
            retry_of=rec.id,
            version_id=rec.version_id,
            config_snapshot=rec.config_snapshot,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "record_id": new_rec.id,
        "execution_key": new_rec.execution_key,
        "retry_of": rec.id,
        "message": "已按同一 execution_key 排队重试",
    }
