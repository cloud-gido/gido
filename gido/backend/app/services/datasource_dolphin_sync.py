# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""GIDO 数据源 ⇄ DolphinScheduler：保存后推送、删除侧镜像删除（失败不阻断 GIDO API）"""
from __future__ import annotations

import logging
from typing import Literal, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client

logger = logging.getLogger(__name__)

PushResultKind = Literal["disabled", "ok", "skipped_type", "error"]


def push_gido_datasource_to_dolphin(db: Session, ds) -> Tuple[PushResultKind, Optional[str]]:
    """
    在数据源已入库且含主键 id 之后调用。
    - disabled: Dolphin 集成关闭（ds_enabled=false）
    - ok: 已 upsert DS 数据源
    - skipped_type: 类型不写 DS JDBC 源（如 hive/kafka/oss）
    - error: 调用 DS API 异常
    """
    ws_id = int(getattr(ds, "workspace_id", 0) or 0)
    cfg = get_dolphin_runtime(db, ws_id or None)
    if not cfg.enabled:
        return ("disabled", None)
    if not ((cfg.url or "").strip() and (cfg.token or "").strip()):
        return ("error", "DolphinScheduler 未配置 API 地址或 token")
    try:
        refresh_ds_client(db, ws_id or None)
        from app.services.dolphin import ds_client

        dolphin_id, _ = ds_client.upsert_gido_datasource(ds)
        if dolphin_id is None:
            return ("skipped_type", (ds.ds_type or "").strip() or "unknown")
        return ("ok", None)
    except Exception as e:
        logger.warning("Dolphin 数据源同步失败 GIDO.ds_id=%s: %s", getattr(ds, "id", "?"), e, exc_info=True)
        return ("error", str(e)[:800])


def dolphin_sync_feedback(kind: PushResultKind, detail: Optional[str]) -> Optional[str]:
    """供 API 展示的短文案；disabled 不向客户端占位。"""
    if kind == "disabled":
        return None
    if kind == "ok":
        return "ok"
    if kind == "skipped_type":
        t = detail or ""
        return f"skipped:类型 {t} 未同步到 Dolphin（调度 SQL 需 mysql/postgresql/doris）"
    return f"error:{detail or 'unknown'}"


def try_delete_gido_datasource_mirror(db: Session, ds) -> None:
    if not getattr(ds, "id", None):
        return
    ws_id = getattr(ds, "workspace_id", None)
    cfg = get_dolphin_runtime(db, ws_id)
    if not cfg.enabled or not ((cfg.url or "").strip() and (cfg.token or "").strip()):
        return
    try:
        refresh_ds_client(db, ws_id)
        from app.services.dolphin import ds_client

        ds_client.delete_gido_datasource_mirror(ds)
    except Exception as e:
        logger.warning(
            "删除 Dolphin 数据源镜像失败 GIDO.ds_id=%s: %s", getattr(ds, "id", "?"), e, exc_info=True
        )
