# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""数据服务 API 发布/下线（供直接调用与审批通过后执行）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.data_service import DataApi
from app.services.data_api_engine import wizard_to_sql


def _resolve_ds(db: Session, api: DataApi):
    from app.api.data_service import _resolve_ds as resolve

    return resolve(db, api)


def execute_data_api_publish(db: Session, api: DataApi, user) -> Dict[str, Any]:
    _resolve_ds(db, api)
    if not (api.sql_template or "").strip() and api.mode != "wizard":
        raise HTTPException(status_code=400, detail="SQL 为空，无法发布")
    if api.mode == "wizard":
        api.sql_template = wizard_to_sql(api.wizard_config or {}, list(api.params or []))
    api.status = "online"
    api.version = (api.version or 0) + 1
    api.published_at = datetime.utcnow()
    api.published_by = user.id
    db.commit()
    return {"message": "已发布", "version": api.version, "status": api.status}


def execute_data_api_offline(db: Session, api: DataApi) -> Dict[str, Any]:
    api.status = "offline"
    db.commit()
    return {"message": "已下线", "status": api.status}
