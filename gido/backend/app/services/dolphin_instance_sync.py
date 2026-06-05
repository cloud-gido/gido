# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
将 DolphinScheduler 上的流程/任务实例同步回 GIDO 库（含 Dolphin 定时调度、未走 /workflows/{id}/run 的运行）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.workspace import NodeInstance, TaskNode, Workflow, WorkflowInstance, Workspace
from app.services.dolphin import map_dolphin_process_instance_state

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


def _name_to_node_id(db: Session, wf: Workflow) -> Dict[str, int]:
    dag = wf.dag_config or {}
    m: Dict[str, int] = {}
    for n in dag.get("nodes", []) or []:
        nid = n.get("node_id")
        if not nid:
            continue
        nm = (n.get("name") or "").strip()
        if nm:
            m[nm] = int(nid)
        node = db.query(TaskNode).filter(TaskNode.id == int(nid)).first()
        if node and (node.name or "").strip():
            m[node.name.strip()] = int(nid)
    return m


def _upsert_node_instances_from_ds_tasks(
    db: Session,
    inst: WorkflowInstance,
    tasks: List[dict],
    name_map: Dict[str, int],
    tz_w: str,
) -> Tuple[bool, int]:
    """
    用 Dolphin 任务实例列表回填 NodeInstance（状态与起止时间）。
    返回 (是否有字段变更, 匹配到的任务行数)。
    """
    changed = False
    touched = 0
    for t in tasks:
        tname = (t.get("name") or "").strip()
        node_id = name_map.get(tname)
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
        if ni:
            if ni.status != t_dw:
                ni.status = t_dw
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
        else:
            db.add(
                NodeInstance(
                    workflow_instance_id=inst.id,
                    node_id=node_id,
                    status=t_dw,
                    started_at=t_start or datetime.utcnow(),
                    finished_at=t_end if t_dw != "running" else None,
                    log_content="",
                )
            )
            changed = True
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


def sync_from_dolphin_definitions(db: Session, ds_client: Any) -> Dict[str, int]:
    """
    对每个已发布到 DS 的工作流（dag 含 ds_project_code / ds_process_code），
    从 Dolphin 拉最近流程实例并 upsert WorkflowInstance；再拉任务实例填充 NodeInstance。
    """
    ingested = 0
    updated = 0
    node_upserted = 0
    cmd_filled = 0
    definitions_scanned = 0

    workflows = db.query(Workflow).order_by(Workflow.id.asc()).all()
    for wf in workflows:
        dag = wf.dag_config or {}
        try:
            pc = int(dag.get("ds_project_code") or 0)
            pcode = int(dag.get("ds_process_code") or 0)
        except (TypeError, ValueError):
            continue
        if not pc or not pcode:
            continue
        definitions_scanned += 1
        name_map = _name_to_node_id(db, wf)
        tz_w = _workspace_tz_for_wf(db, wf)
        try:
            rows = ds_client.list_process_instances(pc, process_definition_code=pcode, page_size=100)
        except Exception as e:
            logger.warning("DS list_process_instances failed wf_id=%s project=%s process=%s: %s", wf.id, pc, pcode, e)
            continue

        for row in rows:
            ds_pi_id = _process_instance_id_from_row(row)
            if ds_pi_id is None:
                continue
            try:
                inst = (
                    db.query(WorkflowInstance)
                    .filter(
                        WorkflowInstance.workflow_id == wf.id,
                        WorkflowInstance.trigger_type.like(f"%ds:{ds_pi_id}%"),
                    )
                    .first()
                )
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

                if inst is None:
                    inst = WorkflowInstance(
                        workflow_id=wf.id,
                        status=dw_status,
                        trigger_type=new_trigger[:128],
                        dolphin_command_type=ct_str,
                        business_date=biz,
                        started_at=started or datetime.utcnow(),
                        finished_at=ended if dw_status != "running" else None,
                    )
                    db.add(inst)
                    db.flush()
                    ingested += 1
                    if ct_str:
                        cmd_filled += 1
                else:
                    changed = False
                    if ct_str and (inst.dolphin_command_type or "") != ct_str:
                        inst.dolphin_command_type = ct_str
                        cmd_filled += 1
                        changed = True
                    if (inst.trigger_type or "") != new_trigger and len(new_trigger) <= 128:
                        inst.trigger_type = new_trigger[:128]
                        changed = True
                    if inst.status != dw_status:
                        inst.status = dw_status
                        changed = True
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

                try:
                    tasks = ds_client.list_task_instances_all(pc, ds_pi_id)
                except Exception as e:
                    logger.debug("DS list_task_instances wf_id=%s pi=%s: %s", wf.id, ds_pi_id, e)
                    tasks = []

                _, n_touched = _upsert_node_instances_from_ds_tasks(db, inst, tasks, name_map, tz_w)
                node_upserted += n_touched

                db.commit()
            except Exception as e:
                logger.warning(
                    "sync_from_dolphin_definitions row failed wf_id=%s process_instance=%s: %s",
                    wf.id,
                    ds_pi_id,
                    e,
                )
                db.rollback()

    return {
        "definitions_scanned": definitions_scanned,
        "ingested": ingested,
        "updated_from_ds": updated,
        "command_types_filled": cmd_filled,
        "node_rows_touched": node_upserted,
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
        .filter(WorkflowInstance.trigger_type.like("%ds:%"))
        .order_by(WorkflowInstance.id.desc())
        .limit(limit)
        .all()
    )
    for inst in candidates:
        try:
            ds_instance_id = int(str(inst.trigger_type).split("ds:")[-1].split("|")[0].strip())
        except (ValueError, IndexError):
            continue
        wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
        if not wf:
            continue
        dag = wf.dag_config or {}
        project_code = dag.get("ds_project_code")
        if not project_code:
            continue
        try:
            ds_info = ds_client.get_instance_status(int(project_code), ds_instance_id)
        except Exception:
            db.rollback()
            continue
        ct = ds_info.get("command_type")
        if ct and (getattr(inst, "dolphin_command_type", None) or "") != str(ct):
            inst.dolphin_command_type = str(ct)[:64]
            cmd_filled += 1
        dw_status = ds_info.get("state_dw") or map_dolphin_process_instance_state(ds_info.get("state"))
        if inst.status != dw_status:
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
        name_map = _name_to_node_id(db, wf)
        try:
            tasks = ds_client.list_task_instances_all(int(project_code), ds_instance_id)
        except Exception:
            tasks = []
        _upsert_node_instances_from_ds_tasks(db, inst, tasks, name_map, tz_w)
        try:
            db.commit()
        except Exception:
            db.rollback()
    return len(candidates), synced, cmd_filled


def _apply_ds_poll_to_instance(db: Session, inst: WorkflowInstance, wf: Workflow, ds_client: Any) -> bool:
    """根据 Dolphin 流程实例详情更新一条工作流实例。返回是否有变更（由调用方 commit）。"""
    dag = wf.dag_config or {}
    project_code = dag.get("ds_project_code")
    if not project_code:
        return False
    try:
        ds_instance_id = int(str(inst.trigger_type).split("ds:")[-1].split("|")[0].strip())
    except (ValueError, IndexError):
        return False
    try:
        ds_info = ds_client.get_instance_status(int(project_code), ds_instance_id)
    except Exception:
        return False
    changed = False
    ct = ds_info.get("command_type")
    if ct and (getattr(inst, "dolphin_command_type", None) or "") != str(ct):
        inst.dolphin_command_type = str(ct)[:64]
        changed = True
    dw_status = ds_info.get("state_dw") or map_dolphin_process_instance_state(ds_info.get("state"))
    if inst.status != dw_status:
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
    name_map = _name_to_node_id(db, wf)
    try:
        tasks = ds_client.list_task_instances_all(int(project_code), ds_instance_id)
    except Exception:
        tasks = []
    node_changed, _ = _upsert_node_instances_from_ds_tasks(db, inst, tasks, name_map, tz_w)
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
            WorkflowInstance.trigger_type.like("%ds:%"),
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
            WorkflowInstance.trigger_type.like("%ds:%"),
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
            WorkflowInstance.trigger_type.like("%ds:%"),
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
