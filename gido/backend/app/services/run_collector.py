# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-09-11
"""
生产运行采集：把执行引擎上的工作流运行持续写回 GIDO 自己的实例事实表。

GIDO 是运行事实的来源，执行引擎（当前 dolphin，后续可接 airflow 等）只是实现细节：
- 采集由后台常驻任务驱动，不依赖用户打开页面
- 采集健康度对产品可见（最近采集时间、是否落后、失败原因），而不是让用户去点「同步」
- 失败运行在采集时写入告警中心为 pending；真正推飞书由独立的出站箱任务负责，
  不在采集持锁路径里打 Webhook（飞书抖动不能拖慢实例账本）
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 后台采集间隔 15s；超过该阈值未成功采集即视为「采集落后」
COLLECT_INTERVAL_SEC = 15
STALE_AFTER_SEC = 90

_HEALTH_CACHE_KEY = "run-collector:health"
_HEALTH_TTL_SEC = 24 * 3600

# 采集互斥锁。与后台任务那把 "scheduler-instance-poll" 是不同的键，
# 嵌套获取不会自锁；那把管「本轮该不该由我这个副本触发」，这把管「同时只能有一轮在采」。
_COLLECT_LOCK_NAME = "run-collector"

_local_health: Dict[str, Any] = {}
_local_lock = threading.Lock()


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(microsecond=0).isoformat() if dt else None


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _read_health() -> Dict[str, Any]:
    try:
        from app.services.shared_state import cache_get

        shared = cache_get(_HEALTH_CACHE_KEY)
        if isinstance(shared, dict):
            return dict(shared)
    except Exception:
        logger.debug("read collector health from shared state failed", exc_info=True)
    with _local_lock:
        return dict(_local_health)


def _write_health(payload: Dict[str, Any]) -> None:
    with _local_lock:
        _local_health.clear()
        _local_health.update(payload)
    try:
        from app.services.shared_state import cache_set

        cache_set(_HEALTH_CACHE_KEY, payload, _HEALTH_TTL_SEC)
    except Exception:
        logger.debug("persist collector health to shared state failed", exc_info=True)


def _record(
    *,
    engine: str,
    enabled: bool,
    stats: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    prev = _read_health()
    now = datetime.utcnow()
    payload: Dict[str, Any] = {
        "engine": engine,
        "enabled": bool(enabled),
        "last_attempt_at": _utc_iso(now),
        "last_success_at": prev.get("last_success_at"),
        "last_error": None,
        "interval_seconds": COLLECT_INTERVAL_SEC,
    }
    if error:
        payload["last_error"] = str(error)[:500]
    else:
        payload["last_success_at"] = _utc_iso(now)
        if stats:
            payload["definitions_scanned"] = int(stats.get("definitions_scanned") or 0)
            payload["runs_ingested"] = int(stats.get("ingested") or 0)
            payload["runs_updated"] = int(stats.get("updated_from_ds") or 0)
            payload["unbound_workflows"] = int(stats.get("skipped_unbound") or 0)
    _write_health(payload)
    return payload


def collector_health(db: Optional[Session] = None) -> Dict[str, Any]:
    """供实例中心/告警中心展示：运行数据是否在持续采集，落后多久。"""
    health = _read_health()
    engine = health.get("engine") or "dolphin"
    enabled = health.get("enabled")
    if enabled is None and db is not None:
        try:
            from app.services.ds_runtime import get_dolphin_runtime

            enabled = bool(get_dolphin_runtime(db).enabled)
        except Exception:
            enabled = None
    last_success = _parse_iso(health.get("last_success_at"))
    lag_seconds: Optional[int] = None
    if last_success:
        lag_seconds = max(int((datetime.utcnow() - last_success).total_seconds()), 0)
    # 「尚未采集」≠「已落后」。落后是曾经采到过、现在超过阈值没再成功；
    # 一次都没成功过只是首次等待，不该挂成红色故障。
    stale = bool(enabled) and lag_seconds is not None and lag_seconds > STALE_AFTER_SEC
    return {
        "engine": engine,
        "enabled": bool(enabled) if enabled is not None else None,
        "interval_seconds": COLLECT_INTERVAL_SEC,
        "last_attempt_at": health.get("last_attempt_at"),
        "last_success_at": health.get("last_success_at"),
        "lag_seconds": lag_seconds,
        "stale": stale,
        "last_error": health.get("last_error"),
        "definitions_scanned": health.get("definitions_scanned"),
        "runs_ingested": health.get("runs_ingested"),
        "unbound_workflows": health.get("unbound_workflows"),
    }


def collect_runs(db: Session, *, workspace_id: Optional[int] = None, page_size: int = 100) -> Dict[str, Any]:
    """
    采集一轮运行数据。全局同一时刻只允许跑一轮，拿不到锁就直接跳过。

    锁必须放在这里而不是调用方：`scheduler_run_key` 上有唯一索引，两轮采集并发时
    后插入的一方会在 Postgres 里等先插入那个事务提交完才知道算不算冲突
    （`wait_event = transactionid`）。而先插入那一方的事务里还夹着对引擎的 HTTP 调用，
    于是等待方一路堆积、CPU 打满。之前锁只加在后台任务上，而 API 里的手动采集
    和页面兜底采集都是直接调这个函数的，等于没锁——事故就是这么来的。
    """
    from app.services.distributed_lock import try_distributed_lock

    with try_distributed_lock(_COLLECT_LOCK_NAME) as acquired:
        if not acquired:
            logger.info("已有一轮采集在跑，本次跳过 ws=%s", workspace_id)
            return {
                "collected": False,
                "engine": "dolphin",
                "reason": "already_running",
            }
        out = _collect_runs_unlocked(db, workspace_id=workspace_id, page_size=page_size)
    # 锁已释放：顺手扫一轮出站箱。失败反映延迟≈采集周期，而不是再等投递周期；
    # Webhook 抖动也只拖这一小段，不会占着采集锁。
    if out.get("collected"):
        from app.services.alert_notification import kick_alert_dispatch

        kick_alert_dispatch()
    return out


def _collect_runs_unlocked(
    db: Session, *, workspace_id: Optional[int] = None, page_size: int = 100
) -> Dict[str, Any]:
    """真正干活的一轮采集。只应由 `collect_runs` 在持锁状态下调用。"""
    from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
    from app.services.dolphin import ds_client
    from app.services.dolphin_instance_sync import (
        patch_instances_from_ds_detail,
        sync_from_dolphin_definitions,
    )

    if not get_dolphin_runtime(db).enabled:
        _record(engine="dolphin", enabled=False)
        return {"collected": False, "engine": "dolphin", "reason": "scheduler_disabled"}

    try:
        refresh_ds_client(db)
        stats = sync_from_dolphin_definitions(
            db, ds_client, workspace_id=workspace_id, page_size=page_size
        )
        checked, synced, cmd_filled = patch_instances_from_ds_detail(db, ds_client, limit=120)
    except Exception as e:
        _record(engine="dolphin", enabled=True, error=str(e))
        logger.warning("运行采集失败 ws=%s: %s", workspace_id, e, exc_info=True)
        raise

    # 本空间采成功也算健康：证明连通性和凭证没问题。
    # 以前只在全量采集时写健康度，用户点「立即采集」采的是当前空间，
    # 成功了红条还挂着「尚未采集」，体感像平台坏了。
    _record(engine="dolphin", enabled=True, stats=stats)
    out: Dict[str, Any] = {"collected": True, "engine": "dolphin", **stats}
    out["detail_checked"] = checked
    out["finalized_from_detail"] = synced
    out["command_types_filled"] = int(stats.get("command_types_filled") or 0) + cmd_filled
    return out
