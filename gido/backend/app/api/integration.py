# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
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
    sync_config: Optional[SyncConfigIn] = None
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


@router.get("/datasources/{datasource_id}/tables")
def datasource_tables(
    datasource_id: int,
    keyword: str = Query("", max_length=128),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ds = require_datasource_row(db, current_user, datasource_id)
    try:
        return {"tables": list_tables(ds, keyword)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasource-columns")
def get_datasource_columns(
    datasource_id: int,
    table_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ds = require_datasource_row(db, current_user, datasource_id)
    try:
        return {"columns": list_columns(ds, table_name)}
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
    if "sync_config" in data:
        sc = body.sync_config
        data["sync_config"] = _sync_config_dump(sc) if sc is not None else task.sync_config
    ws = task.workspace_id
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


@router.post("/tasks/{task_id}/run")
def run_sync_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    assert_workspace_data_capability(db, current_user, task.workspace_id, "developer", PC.GIDO_BATCH_INTEGRATION_RUN)
    if not task.is_active:
        raise HTTPException(status_code=400, detail="任务已停用")
    try:
        record = start_sync_async(task_id, trigger_type="manual")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "record_id": record.id,
        "status": record.status,
        "message": "同步已在后台执行，请在运行历史中查看进度",
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
