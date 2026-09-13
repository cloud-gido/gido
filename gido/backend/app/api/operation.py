# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, load_only
from sqlalchemy import and_, desc, func, or_, text
from typing import Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.core.access import is_platform_admin
from app.models.workspace import WorkflowInstance, NodeInstance, TaskNode, Workflow, User, Workspace
from app.services.rbac import assert_workspace_data_capability, require_node_instance
from app.services.workflow_trigger_display import (
    RUN_TYPES,
    classify_run_type,
    format_trigger_type_label,
    parse_dolphin_process_instance_id,
)
from app.services.ds_runtime import get_dolphin_runtime
from app.services.run_diagnosis import diagnose_workflow_run
from app.services.dolphin_instance_sync import refresh_ds_workflow_instance_from_dolphin
from app.services.run_collector import collector_health

router = APIRouter(prefix="/operation", tags=["运维中心"])
_log = logging.getLogger(__name__)
# 运行类型标签计数的进程内短缓存（与 Redis 缓存同 TTL），无 Redis 时切标签也不该连打四次重查询
_run_type_count_local: dict[str, tuple[float, dict]] = {}

# 仅统计/展示「工作流提交」产生的实例：NodeInstance 必须挂 WorkflowInstance（排除数据开发里单节点试跑）


def _run_type_condition(run_type: str):
    """
    把 classify_run_type 的判定翻成 SQL，保证分页和计数与列表展示一致。
    引擎回填的 commandType 优先；没有时才看 trigger_type 前缀。
    """
    from app.services.workflow_trigger_display import _COMMAND_KEYWORDS, _TRIGGER_PREFIXES

    cmd = WorkflowInstance.dolphin_command_type
    tt = WorkflowInstance.trigger_type
    has_cmd = and_(cmd.isnot(None), cmd != "")

    def cmd_matches(target: str):
        return or_(*[cmd.ilike(f"%{k}%") for k in _COMMAND_KEYWORDS[target]])

    def trigger_matches(target: str):
        prefixes = _TRIGGER_PREFIXES[target]
        return or_(*[or_(tt == p, tt.like(f"{p}|%")) for p in prefixes])

    by_cmd = and_(has_cmd, cmd_matches(run_type))
    # commandType 认不出来时才轮到 trigger_type，和 classify_run_type 的顺序一致
    cmd_known = or_(*[cmd_matches(t) for t in _COMMAND_KEYWORDS])
    fallback = or_(~has_cmd, ~cmd_known)
    if run_type == "manual":
        known_trigger = or_(*[trigger_matches(t) for t in _TRIGGER_PREFIXES if t != "manual"])
        # 认不出的一律进手动视图，避免它们从所有标签页里消失
        by_trigger = and_(fallback, or_(trigger_matches("manual"), tt.is_(None), ~known_trigger))
    else:
        by_trigger = and_(fallback, trigger_matches(run_type))
    return or_(by_cmd, by_trigger)


def _error_ranking(base_query, since: datetime, limit: int = 5) -> list[dict]:
    """近 7 日出错排行：先看谁一直在红，而不是逐条翻实例。"""
    rows = (
        base_query.filter(
            WorkflowInstance.created_at >= since,
            WorkflowInstance.status == "failed",
        )
        .with_entities(Workflow.id, Workflow.name, func.count(WorkflowInstance.id).label("cnt"))
        .group_by(Workflow.id, Workflow.name)
        .order_by(func.count(WorkflowInstance.id).desc())
        .limit(limit)
        .all()
    )
    return [{"workflow_id": wid, "workflow_name": name or "", "failed_count": int(cnt)} for wid, name, cnt in rows]


def _duration_ranking(base_query, since: datetime, limit: int = 5) -> list[dict]:
    """近 7 日耗时排行：找出最可能顶到基线的作业。在 SQL 里聚合，禁止把 7 日全量实例拉进内存。"""
    bind = base_query.session.get_bind() if hasattr(base_query, "session") else None
    dialect = getattr(getattr(bind, "dialect", None), "name", "") or ""
    if dialect == "postgresql":
        sec = func.extract("epoch", WorkflowInstance.finished_at - WorkflowInstance.started_at)
    elif dialect == "mysql":
        sec = func.timestampdiff(text("SECOND"), WorkflowInstance.started_at, WorkflowInstance.finished_at)
    else:
        # SQLite / 其它：用 unixepoch 差值；测试库主要走这条
        sec = func.strftime("%s", WorkflowInstance.finished_at) - func.strftime("%s", WorkflowInstance.started_at)
    rows = (
        base_query.filter(
            WorkflowInstance.created_at >= since,
            WorkflowInstance.started_at.isnot(None),
            WorkflowInstance.finished_at.isnot(None),
        )
        .with_entities(
            Workflow.id,
            Workflow.name,
            func.count(WorkflowInstance.id).label("runs"),
            func.avg(sec).label("avg_sec"),
            func.max(sec).label("max_sec"),
        )
        .group_by(Workflow.id, Workflow.name)
        .order_by(func.avg(sec).desc())
        .limit(limit)
        .all()
    )
    out = []
    for wid, name, runs, avg_sec, max_sec in rows:
        out.append({
            "workflow_id": wid,
            "workflow_name": name or "",
            "runs": int(runs or 0),
            "avg_seconds": int(avg_sec or 0),
            "max_seconds": int(max_sec or 0),
        })
    return out


def _assert_ops_list_scope(db: Session, current_user: User, workspace_id: int, include_all: bool) -> bool:
    if include_all:
        if not is_platform_admin(current_user):
            raise HTTPException(status_code=403, detail="仅平台管理员可查看全部工作空间")
        return True
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    return False


def _require_workflow_instance(
    db: Session,
    current_user: User,
    workspace_id: int,
    inst_id: int,
    capability: str,
) -> tuple[WorkflowInstance, Workflow]:
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", capability)
    inst = (
        db.query(WorkflowInstance)
        .join(Workflow)
        .filter(WorkflowInstance.id == inst_id, Workflow.workspace_id == workspace_id)
        .first()
    )
    if not inst:
        raise HTTPException(status_code=404, detail="工作流实例不存在")
    wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return inst, wf


def _local_day_start_utc(db: Session, workspace_id: int) -> datetime:
    """
    工作空间时区「今天 0 点」对应的 UTC 时刻。
    用 UTC 日界线会让上海的用户在早上八点前看到的「今日」还是昨天，对不上任何人的直觉。
    """
    from app.services.alert_oncall import workspace_tz

    tz = workspace_tz(db, workspace_id)
    local_now = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


@router.get("/overview")
def get_overview(
    workspace_id: int,
    include_all_workspaces: bool = Query(False, description="平台管理员：跨工作空间查看"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    工作流级运行概况（`WorkflowInstance` × 当前工作区），不含数据开发单节点试跑。
    - 今日实例：工作空间时区的「今天」新建的工作流实例条数
    - 运行中 / 成功 / 失败：按工作流实例的 `status` 计数
    - 成功率：成功 / (成功 + 失败)，无失败且无成功时为 N/A

    只读 GIDO 自己的表，**不要**在这里同步调执行引擎。页面每 15 秒轮询这个接口，
    引擎一慢请求就挂住、各自占着 DB 会话，连接池被吃干后连告警中心都打不开。
    采集由后台 scheduler_instance_poll 负责，落后与否看返回里的 collector。
    """
    all_ws = _assert_ops_list_scope(db, current_user, workspace_id, include_all_workspaces)
    today_start = _local_day_start_utc(db, workspace_id)
    q = db.query(WorkflowInstance).join(Workflow)
    if not all_ws:
        q = q.filter(Workflow.workspace_id == workspace_id)

    # 状态分布一次 GROUP BY，避免 total/running/failed/success/pending/killed 连打 6 次 COUNT
    status_rows = (
        q.with_entities(WorkflowInstance.status, func.count(WorkflowInstance.id))
        .group_by(WorkflowInstance.status)
        .all()
    )
    by_status = {st: int(cnt) for st, cnt in status_rows}
    total = sum(by_status.values())
    running = by_status.get("running", 0)
    failed = by_status.get("failed", 0)
    success = by_status.get("success", 0)
    today = q.filter(WorkflowInstance.created_at >= today_start).with_entities(
        func.count(WorkflowInstance.id)
    ).scalar() or 0

    # 近 7 日实例趋势（按 UTC 日期）
    trend_start = today_start - timedelta(days=6)
    trend_rows = (
        q.filter(WorkflowInstance.created_at >= trend_start)
        .with_entities(
            func.date(WorkflowInstance.created_at).label("d"),
            WorkflowInstance.status,
            func.count(WorkflowInstance.id),
        )
        .group_by(func.date(WorkflowInstance.created_at), WorkflowInstance.status)
        .all()
    )
    daily_map: dict = {}
    for d, st, cnt in trend_rows:
        key = str(d)
        if key not in daily_map:
            daily_map[key] = {"date": key, "total": 0, "success": 0, "failed": 0, "running": 0}
        daily_map[key]["total"] += cnt
        if st in daily_map[key]:
            daily_map[key][st] += cnt
    daily_trend = []
    for i in range(7):
        day = (trend_start + timedelta(days=i)).date()
        key = str(day)
        daily_trend.append(daily_map.get(key, {"date": key, "total": 0, "success": 0, "failed": 0, "running": 0}))

    status_distribution = [
        {"status": "success", "count": success},
        {"status": "failed", "count": failed},
        {"status": "running", "count": running},
        {"status": "pending", "count": by_status.get("pending", 0)},
        {"status": "killed", "count": by_status.get("killed", 0)},
    ]

    from app.services.publish_approval import pending_approval_count

    return {
        "error_ranking": _error_ranking(q, trend_start),
        "duration_ranking": _duration_ranking(q, trend_start),
        "total_instances": total,
        "today_instances": today,
        "running": running,
        "failed": failed,
        "success": success,
        "success_rate": f"{int(success / (success + failed) * 100)}%" if (success + failed) > 0 else "N/A",
        "daily_trend": daily_trend,
        "status_distribution": status_distribution,
        "pending_approvals": pending_approval_count(db, workspace_id),
        "collector": collector_health(db),
    }


@router.get("/instances")
def list_all_instances(
    workspace_id: int,
    status: Optional[str] = None,
    business_date: Optional[str] = None,
    workflow_id: Optional[int] = Query(None, description="只看某个工作流（概览排行下钻）"),
    run_type: Optional[str] = Query(None, description="运行类型：schedule/backfill/rerun/manual"),
    today_only: bool = Query(False, description="仅工作空间时区「今天」新建的实例，与概览「今日实例」一致"),
    include_all_workspaces: bool = Query(False, description="平台管理员：跨工作空间查看"),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    all_ws = _assert_ops_list_scope(db, current_user, workspace_id, include_all_workspaces)
    q = db.query(WorkflowInstance).join(Workflow)
    if not all_ws:
        q = q.filter(Workflow.workspace_id == workspace_id)
    if status:
        q = q.filter(WorkflowInstance.status == status)
    if business_date:
        q = q.filter(WorkflowInstance.business_date == business_date)
    if workflow_id is not None:
        q = q.filter(WorkflowInstance.workflow_id == int(workflow_id))
    if today_only:
        q = q.filter(WorkflowInstance.created_at >= _local_day_start_utc(db, workspace_id))
    # 标签页计数用「除运行类型外的同一批过滤条件」，否则切标签时数字会跳
    scoped = q
    if run_type:
        if run_type not in RUN_TYPES:
            raise HTTPException(status_code=400, detail=f"运行类型须为 {'/'.join(RUN_TYPES)} 之一")
        # 优先等值过滤落库的 run_type；历史 NULL 行回退旧判定，滚动发布期也不丢数据
        q = q.filter(
            or_(
                WorkflowInstance.run_type == run_type,
                and_(
                    or_(WorkflowInstance.run_type.is_(None), WorkflowInstance.run_type == ""),
                    _run_type_condition(run_type),
                ),
            )
        )
    total = q.with_entities(func.count(WorkflowInstance.id)).scalar() or 0
    # 按 created_at+id 排：有 ix_wi_created_at，能 index scan + limit。
    # 不要用 coalesce(started_at, created_at)：表达式排序常逼全表 filesort，20 条也会等整表扫完。
    instances = (
        q.order_by(
            desc(WorkflowInstance.created_at),
            desc(WorkflowInstance.id),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = _serialize_instance_rows(db, instances)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "run_type_counts": _run_type_counts(
            scoped,
            cache_key=(
                f"ws={workspace_id}|all={int(all_ws)}|st={status or ''}|bd={business_date or ''}"
                f"|wf={workflow_id or ''}|today={int(today_only)}"
            ),
        ),
    }


def _serialize_instance_rows(db: Session, instances: list) -> list:
    """批量拼列表行，禁止在循环里按条查库；节点只聚合计数 + 失败/运行中名称。"""
    if not instances:
        return []
    wf_ids = {int(inst.workflow_id) for inst in instances if inst.workflow_id}
    workflows = {
        int(w.id): w
        for w in db.query(Workflow).filter(Workflow.id.in_(wf_ids)).all()
    } if wf_ids else {}
    ws_ids = {int(w.workspace_id) for w in workflows.values() if w.workspace_id}
    workspaces = {
        int(w.id): w
        for w in db.query(Workspace).filter(Workspace.id.in_(ws_ids)).all()
    } if ws_ids else {}

    wi_ids = [int(inst.id) for inst in instances]
    # 按状态聚合：列表不需要把每个成功节点都拉进内存
    status_counts: dict[int, dict[str, int]] = {wid: {} for wid in wi_ids}
    if wi_ids:
        for wid, st, cnt in (
            db.query(
                NodeInstance.workflow_instance_id,
                NodeInstance.status,
                func.count(NodeInstance.id),
            )
            .filter(NodeInstance.workflow_instance_id.in_(wi_ids))
            .group_by(NodeInstance.workflow_instance_id, NodeInstance.status)
            .all()
        ):
            status_counts.setdefault(int(wid), {})[st or ""] = int(cnt)

    interesting: dict[int, list] = {wid: [] for wid in wi_ids}
    node_ids: set[int] = set()
    if wi_ids:
        for ni in (
            db.query(NodeInstance)
            .options(
                load_only(
                    NodeInstance.id,
                    NodeInstance.workflow_instance_id,
                    NodeInstance.node_id,
                    NodeInstance.status,
                )
            )
            .filter(
                NodeInstance.workflow_instance_id.in_(wi_ids),
                NodeInstance.status.in_(("running", "pending", "failed")),
            )
            .all()
        ):
            interesting.setdefault(int(ni.workflow_instance_id), []).append(ni)
            if ni.node_id:
                node_ids.add(int(ni.node_id))
    node_names = {
        int(n.id): n.name
        for n in db.query(TaskNode).filter(TaskNode.id.in_(node_ids)).all()
    } if node_ids else {}

    owner_ids: set[int] = set()
    for w in workflows.values():
        if w.created_by:
            owner_ids.add(int(w.created_by))
        ub = getattr(w, "updated_by", None)
        if ub:
            owner_ids.add(int(ub))
    usernames = {
        int(u.id): u.username
        for u in (db.query(User).filter(User.id.in_(owner_ids)).all() if owner_ids else [])
    }

    result = []
    for inst in instances:
        wf = workflows.get(int(inst.workflow_id)) if inst.workflow_id else None
        tt = inst.trigger_type
        dct = getattr(inst, "dolphin_command_type", None)
        by_st = status_counts.get(int(inst.id), {})
        node_total = sum(by_st.values())
        node_rows = interesting.get(int(inst.id), [])
        running_nodes = [ni for ni in node_rows if ni.status in ("running", "pending")]
        failed_nodes = [ni for ni in node_rows if ni.status == "failed"]
        duration_seconds = None
        if inst.started_at and inst.finished_at:
            duration_seconds = int((inst.finished_at - inst.started_at).total_seconds())
        ws_row = workspaces.get(int(wf.workspace_id)) if wf and wf.workspace_id else None
        cb = wf.created_by if wf else None
        ub = getattr(wf, "updated_by", None) if wf else None
        result.append({
            "id": inst.id,
            "workflow_id": wf.id if wf else None,
            "workspace_id": wf.workspace_id if wf else None,
            "workspace_name": (ws_row.name if ws_row else "") or "",
            "workflow_name": wf.name if wf else "",
            "workflow_created_by": cb,
            "workflow_created_by_username": usernames.get(int(cb)) if cb else None,
            "workflow_updated_by": ub,
            "workflow_updated_by_username": usernames.get(int(ub)) if ub else None,
            "status": inst.status,
            "trigger_type": tt,
            "dolphin_command_type": dct,
            "scheduler_engine": getattr(inst, "scheduler_engine", None) or "dolphin",
            "scheduler_instance_id": getattr(inst, "scheduler_instance_id", None),
            "trigger_label": format_trigger_type_label(tt, dct, getattr(inst, "scheduler_instance_id", None)),
            "run_type": getattr(inst, "run_type", None) or classify_run_type(tt, dct),
            "status_override": getattr(inst, "status_override", None),
            "override_reason": getattr(inst, "override_reason", None),
            "override_at": inst.override_at.isoformat() if getattr(inst, "override_at", None) else None,
            "parent_instance_id": getattr(inst, "parent_instance_id", None),
            "business_date": inst.business_date,
            "started_at": inst.started_at,
            "finished_at": inst.finished_at,
            "last_synced_at": getattr(inst, "last_synced_at", None),
            "scheduler_state_raw": getattr(inst, "scheduler_state_raw", None),
            "scheduler_error": getattr(inst, "scheduler_error", None),
            "node_total": node_total,
            "running_node_count": len(running_nodes),
            "failed_node_count": len(failed_nodes),
            "current_nodes": [node_names.get(ni.node_id, f"节点#{ni.node_id}") for ni in running_nodes[:5]],
            "failed_nodes": [node_names.get(ni.node_id, f"节点#{ni.node_id}") for ni in failed_nodes[:5]],
            "duration_seconds": duration_seconds,
        })
    return result


def _run_type_counts(scoped_query, *, cache_key: str) -> dict:
    """
    各标签页条数。

    专业 SaaS 做法：标签数字允许与列表差一个轮询周期（~15s），不要每次先扫
    count+max 做指纹——那本身就和列表 count 一样贵。命中短缓存直接返回。

    未回填的 run_type=NULL 行：计入 manual，禁止再对历史行跑 4 次 ILIKE COUNT
    （那会把「20 条列表」拖成秒级，和 LIMIT 无关）。
    """
    import time

    from app.services.shared_state import cache_get, cache_set

    full_key = f"ops-run-type-counts:{cache_key}"
    now = time.monotonic()
    local = _run_type_count_local.get(full_key)
    if local and now - local[0] < 15:
        return dict(local[1])

    cached = cache_get(full_key)
    if isinstance(cached, dict) and all(rt in cached for rt in RUN_TYPES):
        counts = {rt: int(cached.get(rt) or 0) for rt in RUN_TYPES}
        _run_type_count_local[full_key] = (now, counts)
        return counts

    # 一次 GROUP BY 即可；run_type 已落库，不必再扫两列现算
    rows = (
        scoped_query.with_entities(
            WorkflowInstance.run_type,
            func.count(WorkflowInstance.id),
        )
        .group_by(WorkflowInstance.run_type)
        .all()
    )
    counts = {rt: 0 for rt in RUN_TYPES}
    legacy = 0
    for stored, cnt in rows:
        n = int(cnt)
        if stored in counts:
            counts[stored] += n
        elif not stored:
            legacy += n
    if legacy:
        counts["manual"] += legacy
    _run_type_count_local[full_key] = (now, counts)
    try:
        cache_set(full_key, counts, 15)
    except Exception:
        _log.debug("cache run_type_counts failed", exc_info=True)
    return counts


@router.get("/node-instances/{ni_id}/log")
def get_node_log(ni_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ni = require_node_instance(db, current_user, ni_id)
    scheduler_instance_id = None
    from app.services.scheduler_ops import fetch_node_log_payload

    if ni.workflow_instance_id:
        wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
        if wf_inst:
            scheduler_id = getattr(wf_inst, "scheduler_instance_id", None)
            if scheduler_id and str(scheduler_id).strip().lstrip("-").isdigit():
                scheduler_instance_id = int(str(scheduler_id).strip())
            else:
                scheduler_instance_id = parse_dolphin_process_instance_id(wf_inst.trigger_type)
    try:
        payload = fetch_node_log_payload(db, ni)
    except Exception as e:
        _log.warning("fetch_node_log_payload failed ni=%s: %s", ni_id, e, exc_info=True)
        payload = {
            "log": ni.log_content or "",
            "message": "拉取调度日志异常，展示 GIDO 本地记录。",
            "source": "gido",
            "status": "error",
        }
    return {
        "log": payload.get("log") or "",
        "log_source_hint": payload.get("message") or "",
        "log_source": payload.get("source"),
        "log_status": payload.get("status"),
        "scheduler_instance_id": scheduler_instance_id,
        "scheduler_task_instance_id": getattr(ni, "scheduler_task_instance_id", None),
        "scheduler_task_code": getattr(ni, "scheduler_task_code", None),
    }


@router.post("/workflows/{workflow_id}/instances/{inst_id}/stop")
def stop_workflow_instance(
    workflow_id: int,
    inst_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inst, wf = _require_workflow_instance(db, current_user, workspace_id, inst_id, PC.GIDO_BATCH_OPERATION_WRITE)
    if int(inst.workflow_id) != int(workflow_id):
        raise HTTPException(status_code=404, detail="工作流实例不存在")
    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        try:
            from app.services.scheduler_ops import stop_workflow_instance_via_scheduler

            stop_workflow_instance_via_scheduler(db, inst)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"调度引擎停止失败: {e}")
    inst.status = "killed"
    inst.finished_at = datetime.utcnow()
    for node_inst in db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).all():
        if node_inst.status in ("pending", "running"):
            node_inst.status = "killed"
            node_inst.finished_at = datetime.utcnow()
    db.commit()
    return {"message": "已停止工作流实例", "instance_id": inst.id, "status": inst.status}


@router.post("/workflows/{workflow_id}/instances/{inst_id}/refresh")
def refresh_workflow_instance(
    workflow_id: int,
    inst_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inst, wf = _require_workflow_instance(db, current_user, workspace_id, inst_id, PC.GIDO_BATCH_OPERATION_READ)
    if int(inst.workflow_id) != int(workflow_id):
        raise HTTPException(status_code=404, detail="工作流实例不存在")
    refresh_ds_workflow_instance_from_dolphin(db, wf.workspace_id, inst.id)
    db.refresh(inst)
    return {
        "message": "已刷新工作流实例",
        "instance_id": inst.id,
        "status": inst.status,
        "last_synced_at": getattr(inst, "last_synced_at", None),
        "scheduler_error": getattr(inst, "scheduler_error", None),
    }


@router.get("/workflows/{workflow_id}/instances/{inst_id}/dag")
def workflow_instance_dag(
    workflow_id: int,
    inst_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    实例 DAG：这次运行当时的图，每个节点带上自己的运行状态。
    图取实例所属版本的快照而不是当前定义——否则改过图之后回看历史实例会串。
    """
    inst, wf = _require_workflow_instance(db, current_user, workspace_id, inst_id, PC.GIDO_BATCH_OPERATION_READ)
    if int(inst.workflow_id) != int(workflow_id):
        raise HTTPException(status_code=404, detail="工作流实例不存在")

    snapshot = _instance_dag_snapshot(db, inst, wf)
    dag_nodes = snapshot.get("nodes") or []
    edges = [
        {"source": int(e["source"]), "target": int(e["target"])}
        for e in (snapshot.get("edges") or [])
        if e.get("source") is not None and e.get("target") is not None
    ]
    node_ids = [int(n["node_id"]) for n in dag_nodes if n.get("node_id") is not None]
    catalog = {
        t.id: t for t in db.query(TaskNode).filter(TaskNode.id.in_(node_ids)).all()
    } if node_ids else {}
    node_instances = {
        ni.node_id: ni
        for ni in db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).all()
        if ni.node_id
    }

    nodes = []
    for n in dag_nodes:
        nid = n.get("node_id")
        if nid is None:
            continue
        nid = int(nid)
        task = catalog.get(nid)
        ni = node_instances.get(nid)
        duration = None
        if ni is not None and ni.started_at and ni.finished_at:
            duration = int((ni.finished_at - ni.started_at).total_seconds())
        log_text = str(getattr(ni, "log_content", None) or "").strip()
        nodes.append({
            "node_id": nid,
            # 图上用快照里的名字：改名后回看历史实例，显示当时那个名字才对得上引擎日志
            "name": (n.get("name") or getattr(task, "name", None) or f"节点#{nid}"),
            "current_name": getattr(task, "name", None),
            "node_type": (n.get("node_type") or getattr(task, "node_type", None) or "SQL"),
            # 快照里有节点但这次运行没跑到它，状态按 not_run 显示，不要伪装成 pending
            "status": getattr(ni, "status", None) or "not_run",
            "node_instance_id": getattr(ni, "id", None),
            "started_at": getattr(ni, "started_at", None),
            "finished_at": getattr(ni, "finished_at", None),
            "duration_seconds": duration,
            "retry_count": getattr(ni, "retry_count", None),
            "scheduler_task_instance_id": getattr(ni, "scheduler_task_instance_id", None),
            "log_summary": log_text.splitlines()[0][:300] if log_text else "",
        })

    return {
        "instance": {
            "id": inst.id,
            "workflow_id": wf.id,
            "workflow_name": wf.name,
            "workspace_id": wf.workspace_id,
            "status": inst.status,
            "status_override": getattr(inst, "status_override", None),
            "business_date": inst.business_date,
            "started_at": inst.started_at,
            "finished_at": inst.finished_at,
            "version_no": snapshot.get("version_no"),
            # 采集时间与采集报错：图上节点状态是否可信，全看这两个
            "last_synced_at": getattr(inst, "last_synced_at", None),
            "scheduler_error": getattr(inst, "scheduler_error", None),
        },
        "nodes": nodes,
        "edges": edges,
    }


def _instance_dag_snapshot(db: Session, inst: WorkflowInstance, wf: Workflow) -> dict:
    from app.models.workspace import JobVersion

    vid = getattr(inst, "job_version_id", None)
    if vid:
        ver = db.query(JobVersion).filter(JobVersion.id == vid).first()
        snap = getattr(ver, "dag_snapshot", None) or {}
        if snap.get("nodes"):
            return {**snap, "version_no": getattr(ver, "version_no", None)}
    return {**(wf.dag_config or {}), "version_no": None}


@router.post("/workflows/{workflow_id}/instances/{inst_id}/rerun")
def rerun_workflow_instance(
    workflow_id: int,
    inst_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_workflow_instance(db, current_user, workspace_id, inst_id, PC.GIDO_BATCH_OPERATION_WRITE)
    from app.api.workflow import rerun_instance

    return rerun_instance(workflow_id, inst_id, db, current_user)


@router.post("/workflows/{workflow_id}/instances/{inst_id}/retry-failed-nodes")
def retry_failed_nodes(
    workflow_id: int,
    inst_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inst, wf = _require_workflow_instance(db, current_user, workspace_id, inst_id, PC.GIDO_BATCH_OPERATION_WRITE)
    if int(inst.workflow_id) != int(workflow_id):
        raise HTTPException(status_code=404, detail="工作流实例不存在")
    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        try:
            from app.services.scheduler_ops import retry_failed_nodes_via_scheduler

            retry_failed_nodes_via_scheduler(db, inst)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"调度引擎重试失败: {e}")
    inst.status = "running"
    inst.finished_at = None
    for node_inst in db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id, NodeInstance.status == "failed").all():
        node_inst.status = "running"
        node_inst.finished_at = None
        node_inst.retry_count = (node_inst.retry_count or 0) + 1
    db.commit()
    return {"message": "已提交失败节点重试", "instance_id": inst.id}


@router.post("/node-instances/{ni_id}/kill")
def kill_node_instance(ni_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ni = require_node_instance(db, current_user, ni_id, "developer", PC.GIDO_BATCH_OPERATION_WRITE)
    from app.services.scheduler_ops import kill_node_via_scheduler

    wf = None
    wf_inst = None
    if ni.workflow_instance_id:
        wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
        if wf_inst:
            wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
    if wf and get_dolphin_runtime(db, wf.workspace_id).enabled:
        try:
            kill_node_via_scheduler(db, ni, workspace_id=wf.workspace_id)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"调度引擎终止失败: {e}")
    ni.status = "killed"
    ni.finished_at = datetime.utcnow()
    if wf_inst:
        wf_inst.status = "killed"
        wf_inst.finished_at = datetime.utcnow()
    db.commit()
    return {"message": "已终止"}


@router.post("/node-instances/{ni_id}/retry")
def retry_node_instance(ni_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ni = require_node_instance(db, current_user, ni_id, "developer", PC.GIDO_BATCH_OPERATION_WRITE)
    from app.services.scheduler_ops import retry_node_via_scheduler

    wf = None
    if ni.workflow_instance_id:
        wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
        if wf_inst:
            wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
    if wf and get_dolphin_runtime(db, wf.workspace_id).enabled:
        try:
            retry_node_via_scheduler(db, ni, workspace_id=wf.workspace_id)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"调度引擎重试失败: {e}")
    ni.status = "running" if wf and get_dolphin_runtime(db, wf.workspace_id).enabled else "pending"
    ni.finished_at = None
    ni.retry_count += 1
    if wf_inst := db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first():
        if wf_inst.status in ("failed", "success", "killed"):
            wf_inst.status = "running"
            wf_inst.finished_at = None
    db.commit()
    return {"message": "已提交重试", "retry_count": ni.retry_count}


@router.get("/diagnose")
def diagnose_run(
    workspace_id: int,
    workflow_id: int,
    business_date: Optional[str] = Query(None, description="业务日期 YYYY-MM-DD；留空取最近一次运行"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """运行诊断：为什么这次没跑 / 还没跑完。只读 GIDO 自己的事实，不向执行引擎发请求。"""
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    wf = db.query(Workflow).filter(
        Workflow.id == workflow_id,
        Workflow.workspace_id == workspace_id,
    ).first()
    if not wf:
        raise HTTPException(status_code=404, detail="工作流不存在")
    if business_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", business_date.strip()):
        raise HTTPException(status_code=400, detail="业务日期格式须为 YYYY-MM-DD")
    return diagnose_workflow_run(db, workflow=wf, business_date=business_date)


@router.get("/alerts")
def get_alerts(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    failed = db.query(WorkflowInstance).join(Workflow).filter(
        Workflow.workspace_id == workspace_id,
        WorkflowInstance.status == "failed",
        WorkflowInstance.created_at >= datetime.utcnow() - timedelta(hours=24),
    ).all()
    alerts = []
    for inst in failed:
        wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
        alerts.append({
            "type": "workflow_failed",
            "workflow_name": wf.name if wf else "",
            "instance_id": inst.id,
            "business_date": inst.business_date,
            "time": inst.finished_at,
        })
    return {"alerts": alerts, "count": len(alerts)}
