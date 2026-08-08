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
from app.services.data_api_bundle import apply_pending_definition
from app.services.data_api_engine import wizard_to_sql
from app.services.data_api_schema import columns_from_wizard_config, persist_response_fields_if_needed


def _resolve_ds(db: Session, api: DataApi):
    from app.api.data_service import _resolve_ds as resolve

    return resolve(db, api)


def execute_data_api_publish(db: Session, api: DataApi, user) -> Dict[str, Any]:
    """发布：若有 pending_definition，先原子切到线上字段，再标记 online。"""
    applied_pending = apply_pending_definition(db, api)
    _resolve_ds(db, api)
    if not (api.sql_template or "").strip() and api.mode != "wizard":
        raise HTTPException(status_code=400, detail="SQL 为空，无法发布")
    if api.mode == "wizard":
        api.sql_template = wizard_to_sql(api.wizard_config or {}, list(api.params or []))
        # 仅写元数据契约；不改变开放网关返回 JSON
        persist_response_fields_if_needed(db, api, columns_from_wizard_config(api.wizard_config))
    api.status = "online"
    api.version = (api.version or 0) + 1
    api.published_at = datetime.utcnow()
    api.published_by = user.id
    api.pending_definition = None
    db.commit()
    return {
        "message": "已发布" + ("（已切换待发布配置）" if applied_pending else ""),
        "version": api.version,
        "status": api.status,
        "applied_pending": applied_pending,
    }


def execute_data_api_offline(db: Session, api: DataApi) -> Dict[str, Any]:
    api.status = "offline"
    db.commit()
    return {"message": "已下线", "status": api.status}
