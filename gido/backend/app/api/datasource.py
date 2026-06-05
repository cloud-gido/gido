# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import DataSource, User, Workspace
from app.services.rbac import assert_workspace_data_capability
from app.services.datasource_dolphin_sync import (
    dolphin_sync_feedback,
    push_gido_datasource_to_dolphin,
    try_delete_gido_datasource_mirror,
)

router = APIRouter(prefix="/datasources", tags=["数据源"])

# Dolphin / JDBC：库名必填；用户名仅 mysql/pg 必填。doris 可无用户（见 datasource_mysql_user：同步 Dolphin 时用 root 占位）
_JDBC_REQUIRES_DATABASE = frozenset({"mysql", "postgresql", "doris"})
_JDBC_REQUIRES_USERNAME = frozenset({"mysql", "postgresql"})


def _assert_jdbc_database_if_needed(ds_type: Optional[str], database: Optional[str]) -> None:
    t = (ds_type or "").strip().lower()
    if t not in _JDBC_REQUIRES_DATABASE:
        return
    if not (database or "").strip():
        raise HTTPException(
            status_code=400,
            detail="mysql、postgresql、doris 须填写数据库名（库名/catalog），此为 DolphinScheduler 同步与 JDBC 连接必填项；留空将导致保存后在 Dolphin 侧同步失败。",
        )


def _assert_jdbc_username_if_needed(ds_type: Optional[str], username: Optional[str]) -> None:
    t = (ds_type or "").strip().lower()
    if t not in _JDBC_REQUIRES_USERNAME:
        return
    if not (username or "").strip():
        raise HTTPException(
            status_code=400,
            detail="mysql、postgresql 须填写用户名；同步到 Dolphin 后 JDBC 不接受空用户。",
        )


class DataSourceCreate(BaseModel):
    workspace_id: int
    name: str
    ds_type: str  # mysql/postgresql/doris/hive/kafka/oss
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    extra_config: Optional[Dict[str, Any]] = None


class DataSourceUpdate(BaseModel):
    """更新时不允许修改 workspace_id，数据源与工作空间绑定。"""

    name: Optional[str] = None
    ds_type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    extra_config: Optional[Dict[str, Any]] = None


class DataSourceOut(BaseModel):
    id: int
    workspace_id: int
    name: str
    ds_type: str
    host: Optional[str]
    port: Optional[int]
    database: Optional[str]
    username: Optional[str]
    is_active: bool
    dolphin_sync: Optional[str] = None  # ok / skipped:… / error:…；未启用 Dolphin 时为 null

    class Config:
        from_attributes = True


def _with_dolphin(db: Session, ds: DataSource, do_push: bool) -> DataSourceOut:
    feed = None
    if do_push:
        kind, detail = push_gido_datasource_to_dolphin(db, ds)
        feed = dolphin_sync_feedback(kind, detail)
    base = DataSourceOut.model_validate(ds)
    return base.model_copy(update={"dolphin_sync": feed})


@router.get("", response_model=List[DataSourceOut])
def list_datasources(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_BATCH_DATASOURCE_READ)
    return db.query(DataSource).filter(DataSource.workspace_id == workspace_id).all()


@router.post("", response_model=DataSourceOut)
def create_datasource(ds_in: DataSourceCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not db.query(Workspace).filter(Workspace.id == ds_in.workspace_id).first():
        raise HTTPException(status_code=404, detail="工作空间不存在")
    # 本空间的空间管理员(admin/负责人)；非全权成员需同时具备平台 gido:batch:datasource:write。
    assert_workspace_data_capability(db, current_user, ds_in.workspace_id, "admin", PC.GIDO_BATCH_DATASOURCE_WRITE)
    _assert_jdbc_database_if_needed(ds_in.ds_type, ds_in.database)
    _assert_jdbc_username_if_needed(ds_in.ds_type, ds_in.username)
    ds = DataSource(**ds_in.model_dump(), created_by=current_user.id)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return _with_dolphin(db, ds, do_push=True)


@router.get("/{ds_id}", response_model=DataSourceOut)
def get_datasource(ds_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ds = db.query(DataSource).filter(DataSource.id == ds_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    assert_workspace_data_capability(db, current_user, ds.workspace_id, "viewer", PC.GIDO_BATCH_DATASOURCE_READ)
    return ds


@router.put("/{ds_id}", response_model=DataSourceOut)
def update_datasource(ds_id: int, ds_in: DataSourceUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ds = db.query(DataSource).filter(DataSource.id == ds_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    assert_workspace_data_capability(db, current_user, ds.workspace_id, "admin", PC.GIDO_BATCH_DATASOURCE_WRITE)
    for k, v in ds_in.model_dump(exclude_unset=True).items():
        if k == "password" and (v is None or v == ""):
            continue
        setattr(ds, k, v)
    _assert_jdbc_database_if_needed(ds.ds_type, ds.database)
    _assert_jdbc_username_if_needed(ds.ds_type, ds.username)
    db.commit()
    db.refresh(ds)
    return _with_dolphin(db, ds, do_push=True)


@router.delete("/{ds_id}")
def delete_datasource(ds_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ds = db.query(DataSource).filter(DataSource.id == ds_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    assert_workspace_data_capability(db, current_user, ds.workspace_id, "admin", PC.GIDO_BATCH_DATASOURCE_WRITE)
    try_delete_gido_datasource_mirror(db, ds)
    db.delete(ds)
    db.commit()
    return {"message": "删除成功"}


@router.post("/{ds_id}/test")
def test_connection(ds_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ds = db.query(DataSource).filter(DataSource.id == ds_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    assert_workspace_data_capability(db, current_user, ds.workspace_id, "viewer", PC.GIDO_BATCH_DATASOURCE_READ)
    _assert_jdbc_database_if_needed(ds.ds_type, ds.database)
    _assert_jdbc_username_if_needed(ds.ds_type, ds.username)
    try:
        if ds.ds_type in ("mysql", "doris"):
            import pymysql
            from app.services.datasource_mysql_user import mysql_protocol_connect_user

            conn = pymysql.connect(
                host=ds.host,
                port=ds.port or 3306,
                user=mysql_protocol_connect_user(ds),
                password=ds.password or "",
                database=ds.database or "",
            )
            conn.close()
        elif ds.ds_type == "postgresql":
            import psycopg2
            conn = psycopg2.connect(host=ds.host, port=ds.port or 5432, user=ds.username, password=ds.password or "", dbname=ds.database or "")
            conn.close()
        return {"status": "success", "message": "连接成功"}
    except Exception as e:
        return {"status": "failed", "message": str(e)}
