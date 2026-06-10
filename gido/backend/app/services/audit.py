# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""审计日志服务"""
from sqlalchemy.orm import Session
from datetime import datetime


def log_action(db: Session, user_id: int, action: str, resource_type: str,
               resource_id: int = None, resource_name: str = None,
               workspace_id: int = None, detail: dict = None, ip: str = None):
    from app.models.workspace import AuditLog
    try:
        db.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            detail=detail or {},
            ip_address=ip,
        ))
        db.commit()
    except Exception:
        db.rollback()
