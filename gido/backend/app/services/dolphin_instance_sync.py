# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""
将 DolphinScheduler 上的流程/任务实例同步回 GIDO 库（含 Dolphin 定时调度、未走 /workflows/{id}/run 的运行）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.workspace import (
    JobVersion,
    NodeInstance,
    SchedulerSyncCursor,
    TaskNode,
    Workflow,
    WorkflowInstance,
    Workspace,
)
from app.services.dolphin import map_dolphin_process_instance_state
from app.services.instance_override import is_status_pinned

logger = logging.getLogger(__name__)

# 纯数字时间戳（秒/毫秒），与「2026-01-01 12:00:00」区分
_TS_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def _trigger_prefix_from_ds_command_type(command_type: Optional[str]) -> str:
    """Dolphin commandType → GIDO trigger_type 前缀段（与 workflow_trigger_display 语义一致）。"""
    if not command_type:
        return "manual"
    u = str(command_type).upper().replace(" ", "_")
    if "COMPLEMENT" in u:
        return "batch"
    if any(x in u for x in ("SCHEDULER", "START_TIMER", "TIMER")):
        return "schedule"
    if "REPEAT" in u or "RECOVER" in u or "RECOVERY" in u:
        return "rerun"
    return "manual"


def _safe_zoneinfo(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo((tz_name or "").strip() or "Asia/Shanghai")
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _workspace_tz_for_wf(db: Session, wf: Workflow) -> str:
    ws = db.query(Workspace).filter(Workspace.id == wf.workspace_id).first()
    t = (ws and ws.timezone) or ""
    return t.strip() or "Asia/Shanghai"


def _parse_dolphin_api_time(val: Any, tz_name: str) -> Optional[datetime]:
    """
    Dolphin REST 返回的无偏移时间字符串表示 **Dolphin 服务器时区（常见与业务同为 Asia/Shanghai）的墙钟时间**，
    不是 UTC。此处按 tz_name 解析后转为 **UTC naive**，与 GIDO 其余 `datetime.utcnow()` 写入一致，
    前端再统一按「UTC naive → 工作区展示」格式化，避免少 8 小时或多 8 小时。
    纯数字按 UTC 毫秒/秒时间戳解析。
    """
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        if val.tzinfo is not None:
            return val.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        return val
    s = str(val).strip()
    if not s:
        return None
    if _TS_NUMERIC.fullmatch(s) and len(s) >= 10 and ":" not in s:
        try:
            n = float(s)
            if n > 1e12:
                return datetime.utcfromtimestamp(n / 1000.0)
            if n > 1e9:
                return datetime.utcfromtimestamp(n)
        except (ValueError, OSError):
            pass
    tz = _safe_zoneinfo(tz_name)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            local_naive = datetime.strptime(s[:26], fmt)
            aware = local_naive.replace(tzinfo=tz)
            return aware.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _process_instance_id_from_row(row: dict) -> Optional[int]:
    rid = None
    for key in ("id", "processInstanceId", "process_instance_id"):
        if row.get(key) is not None:
            rid = row.get(key)
            break
    if rid is None:
        return None
    if isinstance(rid, int):
        return rid
    if isinstance(rid, float):
        return int(rid)
    s = str(rid).strip()
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    return None


def _scheduler_instance_id_from_inst(inst: WorkflowInstance) -> Optional[int]:
    raw = getattr(inst, "scheduler_instance_id", None)
    if raw is not None and str(raw).strip():
        s = str(raw).strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
    try:
        return int(str(inst.trigger_type).split("ds:")[-1].split("|")[0].strip())
    except (ValueError, IndexError, AttributeError):
        return None


def _workflow_scheduler_refs(wf: Workflow) -> Tuple[Optional[int], Optional[int]]:
    project_id = getattr(wf, "scheduler_project_id", None)
    definition_id = getattr(wf, "scheduler_definition_id", None)
    try:
        return int(project_id), int(definition_id)
    except (TypeError, ValueError):
        return None, None


def _instance_project_code(db: Session, inst: WorkflowInstance, wf: Workflow) -> Optional[int]:
    """实例优先使用发布版本里的 project，避免重发/DS 重建后用当前 workflow refs 误查旧实例。"""
    raw_pc = getattr(inst, "scheduler_project_id", None)
    if raw_pc is not None and str(raw_pc).strip():
        try:
            return int(str(raw_pc).strip())
        except (TypeError, ValueError):
            pass
    if getattr(inst, "job_version_id", None):
        ver = db.query(JobVersion).filter(JobVersion.id == inst.job_version_id).first()
        if ver and ver.scheduler_project_id:
            try:
                return int(ver.scheduler_project_id)
            except (TypeError, ValueError):
                pass
    pc, _ = _workflow_scheduler_refs(wf)
    return pc


def _instance_definition_code(db: Session, inst: WorkflowInstance, wf: Workflow) -> Optional[int]:
    raw_def = getattr(inst, "scheduler_definition_id", None)
    if raw_def is not None and str(raw_def).strip():
        try:
            return int(str(raw_def).strip())
        except (TypeError, ValueError):
            pass
    if getattr(inst, "job_version_id", None):
        ver = db.query(JobVersion).filter(JobVersion.id == inst.job_version_id).first()
        if ver and ver.scheduler_definition_id:
            try:
                return int(ver.scheduler_definition_id)
            except (TypeError, ValueError):
                pass
    _, definition_id = _workflow_scheduler_refs(wf)
    return definition_id


def _scheduler_run_key(engine: str, project_id: Any, definition_id: Any, instance_id: Any) -> str:
    return f"{engine or 'dolphin'}:{project_id or ''}:{definition_id or ''}:{instance_id or ''}"[:128]


def _row_raw_state(row: dict) -> Optional[str]:
    for key in ("state", "executionStatus", "execution_status", "processInstanceState", "process_instance_state"):
        if row.get(key) is not None and str(row.get(key)).strip() != "":
            return str(row.get(key)).strip()[:128]
    return None


def _mark_instance_scheduler_lost(inst: WorkflowInstance, reason: str) -> bool:
    if is_status_pinned(inst):
        return False
    if inst.status not in ("running", "pending"):
        return False
    inst.status = "failed"
    inst.finished_at = datetime.utcnow()
    inst.scheduler_error = reason[:2000] if reason else None
    inst.last_synced_at = datetime.utcnow()
    if reason:
        existing = str(inst.trigger_type or "")
        if "scheduler_lost" not in existing:
            inst.trigger_type = (existing + "|scheduler_lost")[:128] if existing else "scheduler_lost"
    return True


def _active_version_id(db: Session, wf: Workflow) -> Optional[int]:
    if getattr(wf, "active_version_id", None):
        return int(wf.active_version_id)
    row = (
        db.query(JobVersion)
        .filter(JobVersion.workflow_id == wf.id, JobVersion.status == "active")
        .order_by(JobVersion.version_no.desc(), JobVersion.id.desc())
        .first()
    )
    return row.id if row else None


def _task_instance_id_from_row(row: dict) -> Optional[str]:
    for key in ("id", "taskInstanceId", "task_instance_id"):
        if row.get(key) is not None:
            return str(row.get(key))
    return None


def _task_code_from_row(row: dict) -> Optional[str]:
    for key in ("taskCode", "task_code", "taskDefinitionCode", "task_definition_code"):
        if row.get(key) is not None:
            return str(row.get(key))
    return None


class _NodeResolver:
    """
    把引擎任务行对回 GIDO 节点。优先用发布时写下的 task code：节点改名后引擎里还是旧名字，
    只按名字匹配会整片丢掉节点明细。名字仅作为老实例（发布时还没有 task code）的兜底。
    """

    def __init__(self, by_code: Dict[str, int], by_name: Dict[str, int]):
        self.by_code = by_code
        self.by_name = by_name

    def resolve(self, task_row: dict) -> Optional[int]:
        code = _task_code_from_row(task_row)
        if code and code in self.by_code:
            return self.by_code[code]
        name = (task_row.get("name") or "").strip()
        if name and name in self.by_name:
            return self.by_name[name]
        return None


def _instance_dag_nodes(db: Session, inst: WorkflowInstance, wf: Workflow) -> List[dict]:
    """用这次运行所属版本的 DAG 快照，而不是可能已被改过的当前定义。"""
    vid = getattr(inst, "job_version_id", None)
    if vid:
        ver = db.query(JobVersion).filter(JobVersion.id == vid).first()
        snapshot = getattr(ver, "dag_snapshot", None) or {}
        nodes = snapshot.get("nodes") or []
        if nodes:
            return nodes
    return (wf.dag_config or {}).get("nodes") or []


def _node_resolver(db: Session, inst: WorkflowInstance, wf: Workflow) -> _NodeResolver:
    by_code: Dict[str, int] = {}
    by_name: Dict[str, int] = {}
    for n in _instance_dag_nodes(db, inst, wf):
        nid = n.get("node_id")
        if not nid:
            continue
        code = n.get("ds_task_code")
        if code is not None and str(code).strip():
            by_code[str(code).strip()] = int(nid)
        nm = (n.get("name") or "").strip()
        if nm:
            by_name[nm] = int(nid)
        node = db.query(TaskNode).filter(TaskNode.id == int(nid)).first()
        if node and (node.name or "").strip():
            by_name[node.name.strip()] = int(nid)
    return _NodeResolver(by_code, by_name)


def _alert_workflow_failed(db: Session, inst: WorkflowInstance) -> None:
    try:
        from app.services.alert_center import open_instance_alert

        open_instance_alert(db, workflow_instance=inst, notify=True)
    except Exception:
        logger.warning("open workflow alert failed inst=%s", getattr(inst, "id", None), exc_info=True)


def _alert_workflow_recovered(db: Session, inst: WorkflowInstance) -> None:
    try:
        from app.services.alert_center import resolve_instance_alerts_on_recovery

        resolve_instance_alerts_on_recovery(db, inst)
    except Exception:
        logger.debug("open recovery alert failed", exc_info=True)


def _upsert_node_instances_from_ds_tasks(
    db: Session,
    inst: WorkflowInstance,
    tasks: List[dict],
    resolver: "_NodeResolver",
    tz_w: str,
) -> Tuple[bool, int]:
    """
    用 Dolphin 任务实例列表回填 NodeInstance（状态与起止时间）。
    返回 (是否有字段变更, 匹配到的任务行数)。
    """
    changed = False
    touched = 0
    for t in tasks:
        node_id = resolver.resolve(t)
        if not node_id:
            continue
        t_dw = map_dolphin_process_instance_state(
            t.get("state") or t.get("executionStatus") or t.get("execution_status")
        )
        ni = (
            db.query(NodeInstance)
            .filter(
                NodeInstance.workflow_instance_id == inst.id,
                NodeInstance.node_id == node_id,
            )
            .first()
        )
        t_start = _parse_dolphin_api_time(t.get("startTime") or t.get("start_time"), tz_w)
        t_end = _parse_dolphin_api_time(t.get("endTime") or t.get("end_time"), tz_w)
        task_instance_id = _task_instance_id_from_row(t)
        task_code = _task_code_from_row(t)
        raw_task_state = _row_raw_state(t)
        sync_now = datetime.utcnow()
        if ni:
            if ni.status != t_dw:
                ni.status = t_dw
                changed = True
            snapshot_updates = {
                "scheduler_engine": getattr(inst, "scheduler_engine", None) or "dolphin",
                "scheduler_project_id": getattr(inst, "scheduler_project_id", None),
                "scheduler_definition_id": getattr(inst, "scheduler_definition_id", None),
                "scheduler_instance_id": getattr(inst, "scheduler_instance_id", None),
                "scheduler_state_raw": raw_task_state,
                "last_synced_at": sync_now,
            }
            for key, val in snapshot_updates.items():
                if val is not None and getattr(ni, key, None) != val:
                    setattr(ni, key, val)
                    changed = True
            if t_start and ni.started_at != t_start:
                ni.started_at = t_start
                changed = True
            if t_dw != "running":
                if ni.finished_at != t_end:
                    ni.finished_at = t_end
                    changed = True
            else:
                if ni.finished_at is not None:
                    ni.finished_at = None
                    changed = True
            if task_instance_id and getattr(ni, "scheduler_task_instance_id", None) != task_instance_id:
                ni.scheduler_engine = "dolphin"
                ni.scheduler_task_instance_id = task_instance_id
                changed = True
            if task_code and getattr(ni, "scheduler_task_code", None) != task_code:
                ni.scheduler_engine = "dolphin"
                ni.scheduler_task_code = task_code
                changed = True
        else:
            ni = NodeInstance(
                workflow_instance_id=inst.id,
                node_id=node_id,
                status=t_dw,
                started_at=t_start or datetime.utcnow(),
                finished_at=t_end if t_dw != "running" else None,
                log_content="",
                scheduler_engine=getattr(inst, "scheduler_engine", None) or "dolphin",
                scheduler_project_id=getattr(inst, "scheduler_project_id", None),
                scheduler_definition_id=getattr(inst, "scheduler_definition_id", None),
                scheduler_instance_id=getattr(inst, "scheduler_instance_id", None),
                scheduler_task_instance_id=task_instance_id,
                scheduler_task_code=task_code,
                scheduler_state_raw=raw_task_state,
                last_synced_at=sync_now,
            )
            db.add(ni)
            db.flush()
            changed = True
        if t_dw == "failed":
            try:
                from app.services.alert_center import open_instance_alert

                open_instance_alert(
                    db,
                    workflow_instance=inst,
                    node_instance=ni,
                    message=f"节点 {tname or node_id} 执行失败",
                    notify=False,
                )
            except Exception:
                logger.debug("open node alert failed", exc_info=True)
        touched += 1
    return changed, touched


def _row_command_type(row: dict) -> Optional[str]:
    return row.get("commandType") or row.get("command_type")


def _business_date_from_row(row: dict, tz_name: str) -> Optional[str]:
    st = row.get("scheduleTime") or row.get("schedule_time")
    if st:
        s = str(st).strip()
        if len(s) >= 10:
            return s[:10]
    st2 = row.get("startTime") or row.get("start_time")
    p = _parse_dolphin_api_time(st2, tz_name)
    if p:
        return p.strftime("%Y-%m-%d")
    return None


def _get_sync_cursor(db: Session, engine: str, project_code: Any, definition_code: Any) -> SchedulerSyncCursor:
    """取（或建）某个流程定义的采集水位线。"""
    proj, defn = str(project_code), str(definition_code)
    cursor = (
        db.query(SchedulerSyncCursor)
        .filter(
            SchedulerSyncCursor.scheduler_engine == engine,
            SchedulerSyncCursor.project_id == proj,
            SchedulerSyncCursor.definition_id == defn,
        )
        .first()
    )
    if cursor is not None:
        return cursor
    cursor = SchedulerSyncCursor(
        scheduler_engine=engine, project_id=proj, definition_id=defn, last_instance_id=0
    )
    savepoint = db.begin_nested()
    try:
        db.add(cursor)
        db.flush()
        savepoint.commit()
        return cursor
    except IntegrityError:
        savepoint.rollback()
        return (
            db.query(SchedulerSyncCursor)
            .filter(
                SchedulerSyncCursor.scheduler_engine == engine,
                SchedulerSyncCursor.project_id == proj,
                SchedulerSyncCursor.definition_id == defn,
            )
            .one()
        )


def _advance_sync_cursor(cursor: SchedulerSyncCursor, highest_seen: int) -> None:
    """水位线只进不退：采集失败或漏页时宁可下轮重扫，也不能跳过未入库的实例。"""
    if highest_seen > (cursor.last_instance_id or 0):
        cursor.last_instance_id = highest_seen
    cursor.last_synced_at = datetime.utcnow()


def _find_existing_ds_instance(db: Session, wf: Workflow, ds_pi_id: int) -> Optional[WorkflowInstance]:
    """同一 Dolphin 实例只对应一行，不按当前 active_version 过滤。"""
    inst = (
        db.query(WorkflowInstance)
        .filter(
            WorkflowInstance.workflow_id == wf.id,
            WorkflowInstance.scheduler_instance_id == str(ds_pi_id),
        )
        .order_by(WorkflowInstance.id.desc())
        .first()
    )
    if inst is not None:
        return inst
    candidates = (
        db.query(WorkflowInstance)
        .filter(
            WorkflowInstance.workflow_id == wf.id,
            WorkflowInstance.trigger_type.like(f"%ds:{ds_pi_id}%"),
        )
        .order_by(WorkflowInstance.id.desc())
        .limit(8)
        .all()
    )
    for cand in candidates:
        if _scheduler_instance_id_from_inst(cand) == ds_pi_id:
            return cand
    return None


def _add_instance_idempotent(db: Session, inst: WorkflowInstance) -> tuple[WorkflowInstance, bool]:
    """
    一次引擎运行只落一行。scheduler_run_key 有唯一索引，采集与回调并发时
    后写的一方让位给已存在的那行，而不是抛错中断整轮采集。
    返回 (实例, 是否本次新建)。
    """
    savepoint = db.begin_nested()
    try:
        db.add(inst)
        db.flush()
        savepoint.commit()
        return inst, True
    except IntegrityError:
        savepoint.rollback()
        run_key = getattr(inst, "scheduler_run_key", None)
        existing = (
            db.query(WorkflowInstance).filter(WorkflowInstance.scheduler_run_key == run_key).first()
            if run_key
            else None
        )
        if existing is None:
            raise
        return existing, False


def _call_list_process_instances_page(
    ds_client: Any, project_code: int, process_code: Optional[int], **kwargs: Any
) -> Dict[str, Any]:
    call_kw = dict(kwargs)
    if process_code:
        call_kw["process_definition_code"] = int(process_code)
    page_fn = getattr(ds_client, "list_process_instances_page", None)
    if callable(page_fn):
        try:
            out = page_fn(project_code, **call_kw)
        except TypeError:
            out = None
        if isinstance(out, dict) and "rows" in out:
            try:
                total_page = int(out.get("total_page") or 1)
            except (TypeError, ValueError):
                total_page = 1
            return {"rows": list(out.get("rows") or []), "total_page": max(total_page, 1)}
    try:
        rows = ds_client.list_process_instances(project_code, **call_kw)
    except TypeError:
        rows = ds_client.list_process_instances(
            project_code,
            page_size=int(call_kw.get("page_size") or 100),
        )
    return {"rows": list(rows or []), "total_page": 1}


def _append_instance_rows(rows: List[dict], seen: set[int], merged: List[dict]) -> None:
    for row in rows:
        pid = _process_instance_id_from_row(row)
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        merged.append(row)


def _paged_instance_rows(
    ds_client: Any,
    project_code: int,
    process_code: Optional[int],
    **kwargs: Any,
) -> List[dict]:
    merged: List[dict] = []
    seen: set[int] = set()
    try:
        first = _call_list_process_instances_page(
            ds_client, project_code, process_code, page_no=1, **kwargs
        )
    except Exception as e:
        logger.warning(
            "DS list_process_instances failed project=%s process=%s params=%s: %s",
            project_code,
            process_code,
            kwargs,
            e,
        )
        return merged
    _append_instance_rows(first["rows"], seen, merged)
    last_page = int(first.get("total_page") or 1)
    extra = [p for p in (last_page, last_page - 1) if p > 1]
    for page_no in extra:
        try:
            more = _call_list_process_instances_page(
                ds_client, project_code, process_code, page_no=page_no, **kwargs
            )
        except Exception as e:
            logger.warning(
                "DS list_process_instances page=%s failed project=%s process=%s: %s",
                page_no,
                project_code,
                process_code,
                e,
            )
            continue
        _append_instance_rows(more["rows"], seen, merged)
    return merged


def _definition_code_from_row(row: dict) -> Optional[int]:
    for key in (
        "processDefinitionCode",
        "processDefineCode",
        "process_definition_code",
        "process_define_code",
    ):
        v = row.get(key)
        if v is None or str(v).strip() == "":
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def _list_project_failure_rows(ds_client: Any, project_code: int, tz_name: str) -> List[dict]:
    """项目级最近失败：不按流程定义过滤，末页即 Dolphin 上最新红的那些。"""
    now = datetime.now(_safe_zoneinfo(tz_name))
    start = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    end = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    return _paged_instance_rows(
        ds_client,
        project_code,
        None,
        page_size=100,
        start_date=start,
        end_date=end,
        state_type="FAILURE",
    )


# 单个定义单轮最多翻的页数：水位线追平前的兜底，防止首次接入时一次拉爆
_MAX_CATCHUP_PAGES = 25


def _max_instance_id(rows: List[dict]) -> int:
    ids = [pid for pid in (_process_instance_id_from_row(r) for r in rows) if pid is not None]
    return max(ids) if ids else 0


def _walk_pages_until_watermark(
    ds_client: Any,
    project_code: int,
    process_code: Optional[int],
    *,
    watermark: int,
    page_size: int,
    max_pages: int = _MAX_CATCHUP_PAGES,
    **kwargs: Any,
) -> List[dict]:
    """
    从最新的一侧开始翻页，直到整页实例号都不高于水位线为止。

    Dolphin 不同版本的列表排序不一致（有的 start_time 升序，有的降序），
    所以先比较首页与末页的最大实例号来判断方向，再朝「新」的方向走。
    这样中间页不会像固定窗口那样被永久跳过。
    """
    merged: List[dict] = []
    seen: set[int] = set()

    def fetch(page_no: int) -> Optional[Dict[str, Any]]:
        try:
            return _call_list_process_instances_page(
                ds_client, project_code, process_code, page_no=page_no, page_size=page_size, **kwargs
            )
        except Exception as e:
            logger.warning(
                "DS 实例分页拉取失败 project=%s process=%s page=%s: %s",
                project_code, process_code, page_no, e,
            )
            return None

    first = fetch(1)
    if first is None:
        return merged
    total_page = max(int(first.get("total_page") or 1), 1)
    _append_instance_rows(first["rows"], seen, merged)
    if total_page == 1:
        return merged

    last = fetch(total_page)
    last_rows = last["rows"] if last else []
    _append_instance_rows(last_rows, seen, merged)

    ascending = _max_instance_id(last_rows) > _max_instance_id(first["rows"])
    pages = range(total_page - 1, 0, -1) if ascending else range(2, total_page + 1)

    walked = 0
    for page_no in pages:
        if walked >= max_pages:
            logger.info(
                "追平水位线前达到翻页上限 project=%s process=%s watermark=%s",
                project_code, process_code, watermark,
            )
            break
        page = fetch(page_no)
        if page is None:
            continue
        walked += 1
        rows = page["rows"]
        _append_instance_rows(rows, seen, merged)
        if not rows or _max_instance_id(rows) <= watermark:
            break
    return merged


def _list_ds_process_instance_rows(
    ds_client: Any,
    project_code: int,
    process_code: int,
    *,
    page_size: int,
    tz_name: str,
    watermark: int = 0,
) -> List[dict]:
    """
    增量采集一个流程定义：从水位线往新的方向翻页追平，再补一段 FAILURE 兜底。
    FAILURE 单独扫是因为失败实例可能早于水位线才被引擎标红（如长时间运行后失败）。
    """
    merged: List[dict] = []
    seen: set[int] = set()
    _append_instance_rows(
        _walk_pages_until_watermark(
            ds_client,
            project_code,
            process_code,
            watermark=watermark,
            page_size=max(int(page_size), 50),
        ),
        seen,
        merged,
    )
    now = datetime.now(_safe_zoneinfo(tz_name))
    fail_start = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    fail_end = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    _append_instance_rows(
        _paged_instance_rows(
            ds_client,
            project_code,
            process_code,
            page_size=max(int(page_size), 100),
            start_date=fail_start,
            end_date=fail_end,
            state_type="FAILURE",
        ),
        seen,
        merged,
    )
    return merged


def sync_from_dolphin_definitions(
    db: Session, ds_client: Any, *, workspace_id: Optional[int] = None, page_size: int = 100
) -> Dict[str, int]:
    """
    对每个已发布到生产调度的工作流，从执行引擎拉最近流程实例并 upsert WorkflowInstance；
    再拉任务实例填充 NodeInstance。
    """
    ingested = 0
    updated = 0
    node_upserted = 0
    cmd_filled = 0
    definitions_scanned = 0
    skipped_unbound = 0

    workflows_q = db.query(Workflow).order_by(Workflow.id.asc())
    if workspace_id is not None:
        workflows_q = workflows_q.filter(Workflow.workspace_id == int(workspace_id))
    workflows = workflows_q.all()

    wf_by_def: Dict[tuple[int, int], Workflow] = {}
    projects: set[int] = set()
    for wf in workflows:
        pc, pcode = _workflow_scheduler_refs(wf)
        if pc and pcode:
            wf_by_def[(pc, pcode)] = wf
            projects.add(pc)
    project_fail_by_def: Dict[tuple[int, int], List[dict]] = {}
    for pc in projects:
        tz_name = "Asia/Shanghai"
        for wf in wf_by_def.values():
            wpc, _ = _workflow_scheduler_refs(wf)
            if wpc == pc:
                tz_name = _workspace_tz_for_wf(db, wf)
                break
        for row in _list_project_failure_rows(ds_client, pc, tz_name):
            dcode = _definition_code_from_row(row)
            if not dcode:
                continue
            project_fail_by_def.setdefault((pc, dcode), []).append(row)

    for wf in workflows:
        pc, pcode = _workflow_scheduler_refs(wf)
        if not pc or not pcode:
            if (wf.status == "published") or getattr(wf, "scheduler_definition_id", None) or getattr(
                wf, "scheduler_project_id", None
            ):
                skipped_unbound += 1
            continue
        active_vid = _active_version_id(db, wf)
        definitions_scanned += 1
        tz_w = _workspace_tz_for_wf(db, wf)
        watermark = _get_sync_cursor(db, "dolphin", pc, pcode).last_instance_id or 0
        db.commit()
        rows = _list_ds_process_instance_rows(
            ds_client, pc, pcode, page_size=page_size, tz_name=tz_w, watermark=watermark
        )
        extra_fail = project_fail_by_def.get((pc, pcode), [])
        if extra_fail:
            seen_ids = {pid for pid in (_process_instance_id_from_row(r) for r in rows) if pid is not None}
            _append_instance_rows(extra_fail, seen_ids, rows)
        highest_seen = _max_instance_id(rows)
        row_failures = 0

        for row in rows:
            ds_pi_id = _process_instance_id_from_row(row)
            if ds_pi_id is None:
                continue
            try:
                inst = _find_existing_ds_instance(db, wf, ds_pi_id)
                cmd_type = _row_command_type(row)
                raw_pi_state = row.get("state")
                if raw_pi_state is None or raw_pi_state == "":
                    for key in (
                        "executionStatus",
                        "execution_status",
                        "processInstanceState",
                        "process_instance_state",
                        "workflowExecutionStatus",
                    ):
                        v = row.get(key)
                        if v is not None and str(v).strip() != "":
                            raw_pi_state = v
                            break
                dw_status = map_dolphin_process_instance_state(raw_pi_state)
                prefix = _trigger_prefix_from_ds_command_type(cmd_type)
                new_trigger = f"{prefix}|ds:{ds_pi_id}"
                started = _parse_dolphin_api_time(row.get("startTime") or row.get("start_time"), tz_w)
                ended = _parse_dolphin_api_time(row.get("endTime") or row.get("end_time"), tz_w)
                biz = _business_date_from_row(row, tz_w)
                ct_str = str(cmd_type)[:64] if cmd_type else None
                raw_state_str = str(raw_pi_state)[:128] if raw_pi_state is not None else None
                active_ver = db.query(JobVersion).filter(JobVersion.id == active_vid).first() if active_vid else None
                active_version_no = getattr(active_ver, "version_no", None)
                run_key = _scheduler_run_key("dolphin", pc, pcode, ds_pi_id)
                sync_now = datetime.utcnow()
                became_failed = False
                recovered = False

                if inst is None:
                    inst = WorkflowInstance(
                        workflow_id=wf.id,
                        job_version_id=active_vid,
                        status=dw_status,
                        trigger_type=new_trigger[:128],
                        dolphin_command_type=ct_str,
                        scheduler_engine="dolphin",
                        scheduler_project_id=str(pc),
                        scheduler_definition_id=str(pcode),
                        scheduler_definition_version=active_version_no,
                        scheduler_instance_id=str(ds_pi_id),
                        scheduler_run_key=run_key,
                        scheduler_state_raw=raw_state_str,
                        last_synced_at=sync_now,
                        business_date=biz,
                        started_at=started or datetime.utcnow(),
                        finished_at=ended if dw_status != "running" else None,
                    )
                    inst, created = _add_instance_idempotent(db, inst)
                    if created:
                        ingested += 1
                        if ct_str:
                            cmd_filled += 1
                        became_failed = dw_status == "failed"
                else:
                    changed = False
                    if ct_str and (inst.dolphin_command_type or "") != ct_str:
                        inst.dolphin_command_type = ct_str
                        cmd_filled += 1
                        changed = True
                    if (inst.trigger_type or "") != new_trigger and len(new_trigger) <= 128:
                        inst.trigger_type = new_trigger[:128]
                        changed = True
                    if getattr(inst, "scheduler_engine", None) != "dolphin":
                        inst.scheduler_engine = "dolphin"
                        changed = True
                    snapshot_updates = {
                        "scheduler_project_id": str(pc),
                        "scheduler_definition_id": str(pcode),
                        "scheduler_definition_version": active_version_no,
                        "scheduler_run_key": run_key,
                        "scheduler_state_raw": raw_state_str,
                        "scheduler_error": None,
                        "last_synced_at": sync_now,
                    }
                    for key, val in snapshot_updates.items():
                        if val is not None and getattr(inst, key, None) != val:
                            setattr(inst, key, val)
                            changed = True
                    if getattr(inst, "scheduler_instance_id", None) != str(ds_pi_id):
                        inst.scheduler_instance_id = str(ds_pi_id)
                        changed = True
                    if getattr(inst, "job_version_id", None) is None:
                        vid = _active_version_id(db, wf)
                        if vid:
                            inst.job_version_id = vid
                            changed = True
                    if inst.status != dw_status and not is_status_pinned(inst):
                        recovered = inst.status == "failed" and dw_status == "success"
                        inst.status = dw_status
                        changed = True
                        became_failed = dw_status == "failed"
                    if started and inst.started_at != started:
                        inst.started_at = started
                        changed = True
                    if dw_status != "running":
                        if ended and inst.finished_at != ended:
                            inst.finished_at = ended
                            changed = True
                    elif inst.finished_at is not None:
                        inst.finished_at = None
                        changed = True
                    if biz and not inst.business_date:
                        inst.business_date = biz
                        changed = True
                    if changed:
                        updated += 1

                need_tasks = became_failed or recovered or dw_status in ("failed", "running", "pending")
                if need_tasks:
                    try:
                        tasks = ds_client.list_task_instances_all(pc, ds_pi_id)
                    except Exception as e:
                        logger.debug("DS list_task_instances wf_id=%s pi=%s: %s", wf.id, ds_pi_id, e)
                        tasks = []
                    # 解析器按实例所属版本建，不能在工作流层面复用：同一工作流的不同实例可能跑的是不同版本
                    _, n_touched = _upsert_node_instances_from_ds_tasks(
                        db, inst, tasks, _node_resolver(db, inst, wf), tz_w
                    )
                    node_upserted += n_touched
                if became_failed:
                    logger.info(
                        "ds workflow failed wf_id=%s ds_pi=%s gido_inst=%s, opening alert",
                        wf.id,
                        ds_pi_id,
                        inst.id,
                    )
                    _alert_workflow_failed(db, inst)
                if recovered:
                    _alert_workflow_recovered(db, inst)

                db.commit()
            except Exception as e:
                row_failures += 1
                logger.warning(
                    "sync_from_dolphin_definitions row failed wf_id=%s process_instance=%s: %s",
                    wf.id,
                    ds_pi_id,
                    e,
                )
                db.rollback()

        # 有行没写进去就不推进水位线，下一轮重新扫这一段
        if row_failures == 0 and highest_seen:
            try:
                _advance_sync_cursor(_get_sync_cursor(db, "dolphin", pc, pcode), highest_seen)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning("推进采集水位线失败 project=%s process=%s: %s", pc, pcode, e)

    return {
        "definitions_scanned": definitions_scanned,
        "ingested": ingested,
        "updated_from_ds": updated,
        "command_types_filled": cmd_filled,
        "node_rows_touched": node_upserted,
        "skipped_unbound": skipped_unbound,
    }


def patch_instances_from_ds_detail(
    db: Session, ds_client: Any, *, limit: int = 100
) -> Tuple[int, int, int]:
    """
    对库内 trigger_type 含 ds: 的最近实例，调用 DS 详情补 commandType / 终态（与历史逻辑一致）。
    返回 (checked, synced_status, cmd_filled)
    """
    synced = 0
    cmd_filled = 0
    candidates = (
        db.query(WorkflowInstance)
        .filter(
            (WorkflowInstance.scheduler_instance_id.isnot(None))
            | (WorkflowInstance.trigger_type.like("%ds:%"))
        )
        .order_by(WorkflowInstance.id.desc())
        .limit(limit)
        .all()
    )
    for inst in candidates:
        ds_instance_id = _scheduler_instance_id_from_inst(inst)
        if ds_instance_id is None:
            continue
        wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
        if not wf:
            continue
        project_code = _instance_project_code(db, inst, wf)
        if not project_code:
            continue
        try:
            ds_info = ds_client.get_instance_status(int(project_code), ds_instance_id)
        except Exception as e:
            msg = str(e)
            lowered = msg.lower()
            if any(x in lowered for x in ("not found", "not exist", "does not exist", "not_exists", "不存在")):
                if _mark_instance_scheduler_lost(inst, f"调度实例不存在或已丢失: {msg}"):
                    synced += 1
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                continue
            db.rollback()
            continue
        ct = ds_info.get("command_type")
        if ct and (getattr(inst, "dolphin_command_type", None) or "") != str(ct):
            inst.dolphin_command_type = str(ct)[:64]
            cmd_filled += 1
        if getattr(inst, "scheduler_engine", None) != "dolphin":
            inst.scheduler_engine = "dolphin"
        definition_code = _instance_definition_code(db, inst, wf)
        version_no = None
        if getattr(inst, "job_version_id", None):
            ver = db.query(JobVersion).filter(JobVersion.id == inst.job_version_id).first()
            version_no = getattr(ver, "version_no", None)
        inst.scheduler_project_id = str(project_code)
        if definition_code is not None:
            inst.scheduler_definition_id = str(definition_code)
        if version_no is not None:
            inst.scheduler_definition_version = version_no
        inst.scheduler_run_key = _scheduler_run_key("dolphin", project_code, definition_code, ds_instance_id)
        raw_state = ds_info.get("state")
        inst.scheduler_state_raw = str(raw_state)[:128] if raw_state is not None else None
        inst.scheduler_error = None
        inst.last_synced_at = datetime.utcnow()
        if getattr(inst, "scheduler_instance_id", None) != str(ds_instance_id):
            inst.scheduler_instance_id = str(ds_instance_id)
        dw_status = ds_info.get("state_dw") or map_dolphin_process_instance_state(ds_info.get("state"))
        prev_status = inst.status
        if inst.status != dw_status and not is_status_pinned(inst):
            inst.status = dw_status
            synced += 1
        tz_w = _workspace_tz_for_wf(db, wf)
        st_t = _parse_dolphin_api_time(ds_info.get("startTime") or ds_info.get("start_time"), tz_w)
        if st_t and (not inst.started_at or inst.started_at != st_t):
            inst.started_at = st_t
        end_t = _parse_dolphin_api_time(ds_info.get("endTime") or ds_info.get("end_time"), tz_w)
        if dw_status != "running":
            if end_t:
                inst.finished_at = end_t
            elif inst.finished_at is None:
                inst.finished_at = datetime.utcnow()
        elif inst.finished_at is not None:
            inst.finished_at = None
        try:
            tasks = ds_client.list_task_instances_all(int(project_code), ds_instance_id)
        except Exception:
            tasks = []
        _upsert_node_instances_from_ds_tasks(db, inst, tasks, _node_resolver(db, inst, wf), tz_w)
        # 人工置过状态的实例不再按引擎状态开/关告警，否则置成功后下一轮采集就会把告警重新推出来
        if not is_status_pinned(inst):
            if dw_status == "failed" and prev_status != "failed":
                _alert_workflow_failed(db, inst)
            if prev_status == "failed" and dw_status == "success":
                _alert_workflow_recovered(db, inst)
        try:
            db.commit()
        except Exception:
            db.rollback()
    return len(candidates), synced, cmd_filled


def _apply_ds_poll_to_instance(db: Session, inst: WorkflowInstance, wf: Workflow, ds_client: Any) -> bool:
    """根据 Dolphin 流程实例详情更新一条工作流实例。返回是否有变更（由调用方 commit）。"""
    project_code = _instance_project_code(db, inst, wf)
    if not project_code:
        return False
    ds_instance_id = _scheduler_instance_id_from_inst(inst)
    if ds_instance_id is None:
        return False
    try:
        ds_info = ds_client.get_instance_status(int(project_code), ds_instance_id)
    except Exception as e:
        msg = str(e)
        lowered = msg.lower()
        if any(x in lowered for x in ("not found", "not exist", "does not exist", "not_exists", "不存在")):
            return _mark_instance_scheduler_lost(inst, f"调度实例不存在或已丢失: {msg}")
        logger.debug(
            "get_instance_status failed workflow_instance=%s project=%s ds_instance=%s: %s",
            inst.id,
            project_code,
            ds_instance_id,
            e,
            exc_info=True,
        )
        return False
    changed = False
    ct = ds_info.get("command_type")
    if ct and (getattr(inst, "dolphin_command_type", None) or "") != str(ct):
        inst.dolphin_command_type = str(ct)[:64]
        changed = True
    if getattr(inst, "scheduler_engine", None) != "dolphin":
        inst.scheduler_engine = "dolphin"
        changed = True
    if getattr(inst, "scheduler_instance_id", None) != str(ds_instance_id):
        inst.scheduler_instance_id = str(ds_instance_id)
        changed = True
    definition_code = _instance_definition_code(db, inst, wf)
    version_no = None
    if getattr(inst, "job_version_id", None):
        ver = db.query(JobVersion).filter(JobVersion.id == inst.job_version_id).first()
        version_no = getattr(ver, "version_no", None)
    snapshot_updates = {
        "scheduler_project_id": str(project_code),
        "scheduler_definition_id": str(definition_code) if definition_code is not None else None,
        "scheduler_definition_version": version_no,
        "scheduler_run_key": _scheduler_run_key("dolphin", project_code, definition_code, ds_instance_id),
        "scheduler_state_raw": str(ds_info.get("state"))[:128] if ds_info.get("state") is not None else None,
        "scheduler_error": None,
        "last_synced_at": datetime.utcnow(),
    }
    for key, val in snapshot_updates.items():
        if val is not None and getattr(inst, key, None) != val:
            setattr(inst, key, val)
            changed = True
    if getattr(inst, "job_version_id", None) is None:
        vid = _active_version_id(db, wf)
        if vid:
            inst.job_version_id = vid
            changed = True
    dw_status = ds_info.get("state_dw") or map_dolphin_process_instance_state(ds_info.get("state"))
    prev_status = inst.status
    if inst.status != dw_status and not is_status_pinned(inst):
        inst.status = dw_status
        changed = True
    tz_w = _workspace_tz_for_wf(db, wf)
    st_t = _parse_dolphin_api_time(ds_info.get("startTime") or ds_info.get("start_time"), tz_w)
    if st_t and (not inst.started_at or inst.started_at != st_t):
        inst.started_at = st_t
        changed = True
    end_t = _parse_dolphin_api_time(ds_info.get("endTime") or ds_info.get("end_time"), tz_w)
    if dw_status != "running":
        if end_t and inst.finished_at != end_t:
            inst.finished_at = end_t
            changed = True
        elif inst.finished_at is None:
            inst.finished_at = datetime.utcnow()
            changed = True
    elif dw_status == "running" and inst.finished_at is not None:
        inst.finished_at = None
        changed = True
    try:
        tasks = ds_client.list_task_instances_all(int(project_code), ds_instance_id)
    except Exception:
        tasks = []
    node_changed, _ = _upsert_node_instances_from_ds_tasks(db, inst, tasks, _node_resolver(db, inst, wf), tz_w)
    if not is_status_pinned(inst):
        if dw_status == "failed" and prev_status != "failed":
            _alert_workflow_failed(db, inst)
        if prev_status == "failed" and dw_status == "success":
            _alert_workflow_recovered(db, inst)
    return changed or node_changed


def refresh_ds_workflow_instance_from_dolphin(
    db: Session, workspace_id: int, workflow_instance_id: int
) -> None:
    """
    运维下钻到某一工作流实例时：从 Dolphin 拉流程详情 + 任务实例，写回时间与节点行（与 Dolphin UI 对齐）。
    """
    from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
    from app.services.dolphin import ds_client

    if not get_dolphin_runtime(db).enabled:
        return
    inst = (
        db.query(WorkflowInstance)
        .join(Workflow, Workflow.id == WorkflowInstance.workflow_id)
        .filter(
            Workflow.workspace_id == workspace_id,
            WorkflowInstance.id == workflow_instance_id,
            (WorkflowInstance.scheduler_instance_id.isnot(None))
            | (WorkflowInstance.trigger_type.like("%ds:%")),
        )
        .first()
    )
    if not inst:
        return
    wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
    if not wf:
        return
    refresh_ds_client(db)
    try:
        _apply_ds_poll_to_instance(db, inst, wf, ds_client)
        db.commit()
    except Exception:
        db.rollback()
        logger.debug(
            "refresh_ds_workflow_instance_from_dolphin failed ws=%s wi=%s",
            workspace_id,
            workflow_instance_id,
            exc_info=True,
        )


def refresh_running_ds_instances_for_workspace(db: Session, workspace_id: int, *, limit: int = 35) -> int:
    """
    打开运维页时：把工作区内仍为 running/pending 且挂在 Dolphin 上的实例向 DS 查询并写回终态。
    解决「Dolphin 已结束但库未同步」的问题（无需用户手点同步）。
    """
    from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
    from app.services.dolphin import ds_client

    if not get_dolphin_runtime(db).enabled:
        return 0
    refresh_ds_client(db)
    touched = 0
    rows = (
        db.query(WorkflowInstance)
        .join(Workflow, WorkflowInstance.workflow_id == Workflow.id)
        .filter(
            Workflow.workspace_id == workspace_id,
            WorkflowInstance.status.in_(("running", "pending")),
            (WorkflowInstance.scheduler_instance_id.isnot(None))
            | (WorkflowInstance.trigger_type.like("%ds:%")),
        )
        .order_by(WorkflowInstance.id.desc())
        .limit(limit)
        .all()
    )
    for inst in rows:
        wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
        if not wf:
            continue
        try:
            if _apply_ds_poll_to_instance(db, inst, wf, ds_client):
                touched += 1
            db.commit()
        except Exception:
            db.rollback()
    return touched


def refresh_running_ds_instances_for_workflow(db: Session, wf_id: int, *, limit: int = 45) -> int:
    """工作流「运行历史」抽屉打开时，刷新该工作流下未结束的 Dolphin 实例状态。"""
    from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
    from app.services.dolphin import ds_client

    if not get_dolphin_runtime(db).enabled:
        return 0
    refresh_ds_client(db)
    touched = 0
    wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if not wf:
        return 0
    rows = (
        db.query(WorkflowInstance)
        .filter(
            WorkflowInstance.workflow_id == wf_id,
            WorkflowInstance.status.in_(("running", "pending")),
            (WorkflowInstance.scheduler_instance_id.isnot(None))
            | (WorkflowInstance.trigger_type.like("%ds:%")),
        )
        .order_by(WorkflowInstance.id.desc())
        .limit(limit)
        .all()
    )
    for inst in rows:
        try:
            if _apply_ds_poll_to_instance(db, inst, wf, ds_client):
                touched += 1
            db.commit()
        except Exception:
            db.rollback()
    return touched


def find_published_workflow_for_ds(
    db: Session, *, project_id: Any = None, definition_id: Any = None
) -> Optional[Workflow]:
    """按 DS process definition code（及可选 project）找到已发布到该定义的 GIDO 工作流。"""
    if definition_id is None or not str(definition_id).strip():
        return None
    rows = (
        db.query(Workflow)
        .filter(Workflow.scheduler_definition_id == str(definition_id).strip())
        .order_by(Workflow.id.asc())
        .all()
    )
    if not rows:
        return None
    if project_id is None or not str(project_id).strip():
        return rows[-1]
    pid = str(project_id).strip()
    matched = [w for w in rows if (getattr(w, "scheduler_project_id", None) or "") in ("", pid)]
    return (matched or rows)[-1]


def ingest_ds_instance_from_callback(
    db: Session,
    *,
    scheduler_instance_id: str,
    dw_status: str,
    raw_state: Any = None,
    project_id: Any = None,
    definition_id: Any = None,
) -> Optional[WorkflowInstance]:
    """
    DS 回调时库里还没有该流程实例：按已发布工作流绑定立刻入库。
    失败状态当场写告警并推送，不必等轮询。
    """
    wf = find_published_workflow_for_ds(db, project_id=project_id, definition_id=definition_id)
    if not wf:
        return None
    existing = (
        db.query(WorkflowInstance)
        .filter(
            WorkflowInstance.workflow_id == wf.id,
            WorkflowInstance.scheduler_instance_id == str(scheduler_instance_id),
        )
        .first()
    )
    if existing:
        return existing
    pc, pcode = _workflow_scheduler_refs(wf)
    active_vid = _active_version_id(db, wf)
    proj = str(project_id).strip() if project_id is not None and str(project_id).strip() else (str(pc) if pc else None)
    defn = str(definition_id).strip() if definition_id is not None else (str(pcode) if pcode else None)
    inst = WorkflowInstance(
        workflow_id=wf.id,
        job_version_id=active_vid,
        status=dw_status or "failed",
        trigger_type=f"schedule|ds:{scheduler_instance_id}"[:128],
        scheduler_engine="dolphin",
        scheduler_project_id=proj,
        scheduler_definition_id=defn,
        scheduler_instance_id=str(scheduler_instance_id),
        scheduler_run_key=_scheduler_run_key("dolphin", proj or pc, defn or pcode, scheduler_instance_id),
        scheduler_state_raw=str(raw_state)[:128] if raw_state is not None else None,
        last_synced_at=datetime.utcnow(),
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow() if (dw_status or "failed") != "running" else None,
    )
    inst, created = _add_instance_idempotent(db, inst)
    if created and inst.status == "failed":
        _alert_workflow_failed(db, inst)
    return inst


def sync_workspace_ds_instances(db: Session, workspace_id: int, *, page_size: int = 30) -> Dict[str, int]:
    """打开实例中心时：只同步当前工作空间已发布工作流的最近 DS 实例（含已失败）。"""
    from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
    from app.services.dolphin import ds_client

    empty = {
        "definitions_scanned": 0,
        "ingested": 0,
        "updated_from_ds": 0,
        "command_types_filled": 0,
        "node_rows_touched": 0,
        "skipped_unbound": 0,
    }
    if not get_dolphin_runtime(db).enabled:
        return empty
    refresh_ds_client(db)
    return sync_from_dolphin_definitions(db, ds_client, workspace_id=workspace_id, page_size=page_size)
