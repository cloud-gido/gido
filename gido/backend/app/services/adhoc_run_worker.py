# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""持久化交互式运行队列与增量日志 worker。"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import selectors
import signal
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.workspace import AdhocRun, AdhocRunLogChunk, NodeInstance, TaskNode
from app.services.adhoc_run_store import truncate_result_preview

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"success", "failed", "cancelled", "timed_out"})
ACTIVE_STATUSES = frozenset({"queued", "running", "cancel_requested"})
_dispatcher: Optional[threading.Thread] = None
_running = False
_active_lock = threading.Lock()
_active_threads: Dict[int, threading.Thread] = {}
_active_processes: Dict[int, subprocess.Popen] = {}
_run_secrets: Dict[int, tuple[str, ...]] = {}
_worker_name = f"{socket.gethostname()}:{os.getpid()}"


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _node_execution_key(node_id: int, bizdate: Optional[str], script: str) -> str:
    raw = f"studio:{node_id}:{bizdate or ''}:{_hash(script)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _probe_execution_key(
    user_id: int, datasource_id: int, sql: str, object_name: str
) -> str:
    raw = f"probe:{user_id}:{datasource_id}:{object_name}:{_hash(sql)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _redact_log(run_id: int, content: str) -> str:
    text = str(content or "")
    with _active_lock:
        secrets = _run_secrets.get(run_id, ())
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return re.sub(
        r"(?i)(api[_-]?key|token|password|secret|credential)(\s*[:=]\s*)([^&\s,;]+)",
        r"\1\2***",
        text,
    )


def submit_node_run(
    db: Any,
    *,
    node: TaskNode,
    user_id: int,
    script_content: Optional[str],
    bizdate: Optional[str],
    params: Optional[Dict[str, Any]] = None,
    datasource_id: Optional[int] = None,
) -> tuple[AdhocRun, bool]:
    """创建运行与节点实例；同节点、业务日、脚本活动运行只返回已有记录。"""
    script = node.script_content or "" if script_content is None else script_content
    execution_key = _node_execution_key(node.id, bizdate, script)
    existing = (
        db.query(AdhocRun)
        .filter(AdhocRun.execution_key == execution_key, AdhocRun.status.in_(ACTIVE_STATUSES))
        .first()
    )
    if existing:
        return existing, True

    instance = NodeInstance(node_id=node.id, status="queued", started_at=None)
    db.add(instance)
    db.flush()
    row = AdhocRun(
        workspace_id=node.workspace_id,
        source="studio",
        triggered_by=user_id,
        datasource_id=datasource_id if datasource_id is not None else node.datasource_id,
        object_name=node.name,
        node_id=node.id,
        node_instance_id=instance.id,
        sql_text=script,
        status="queued",
        run_type=(node.node_type or "").upper(),
        business_date=bizdate,
        script_hash=_hash(script),
        execution_key=execution_key,
        request_payload={
            "bizdate": bizdate,
            **({"params": params} if params is not None else {}),
            **({"datasource_id": datasource_id} if datasource_id is not None else {}),
        },
        started_at=None,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
        return row, False
    except IntegrityError:
        db.rollback()
        existing = db.query(AdhocRun).filter(AdhocRun.execution_key == execution_key).first()
        if existing and existing.status in ACTIVE_STATUSES:
            return existing, True
        raise


def submit_probe_run(
    db: Any,
    *,
    workspace_id: int,
    datasource_id: int,
    user_id: int,
    sql: str,
    limit: int,
    object_name: str,
) -> tuple[AdhocRun, bool]:
    execution_key = _probe_execution_key(user_id, datasource_id, sql, object_name)
    existing = (
        db.query(AdhocRun)
        .filter(AdhocRun.execution_key == execution_key, AdhocRun.status.in_(ACTIVE_STATUSES))
        .first()
    )
    if existing:
        return existing, True
    row = AdhocRun(
        workspace_id=workspace_id,
        source="probe",
        triggered_by=user_id,
        datasource_id=datasource_id,
        object_name=object_name,
        sql_text=sql,
        status="queued",
        run_type="PROBE",
        script_hash=_hash(sql),
        execution_key=execution_key,
        request_payload={"limit": int(limit)},
        created_at=datetime.utcnow(),
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
        return row, False
    except IntegrityError:
        db.rollback()
        existing = db.query(AdhocRun).filter(AdhocRun.execution_key == execution_key).first()
        if existing and existing.status in ACTIVE_STATUSES:
            return existing, True
        raise


def append_log(run_id: int, content: str, stream: str = "stdout") -> None:
    text = _redact_log(run_id, content)
    if not text:
        return
    db = SessionLocal()
    try:
        total = (
            db.query(func.coalesce(func.sum(func.length(AdhocRunLogChunk.content)), 0))
            .filter(AdhocRunLogChunk.run_id == run_id)
            .scalar()
            or 0
        )
        if int(total) >= int(settings.ADHOC_LOG_MAX_BYTES):
            return
        seq = (
            db.query(func.coalesce(func.max(AdhocRunLogChunk.seq), 0))
            .filter(AdhocRunLogChunk.run_id == run_id)
            .scalar()
            or 0
        )
        db.add(
            AdhocRunLogChunk(
                run_id=run_id,
                seq=int(seq) + 1,
                stream=stream[:16],
                content=text[: min(len(text), 64 * 1024)],
                created_at=datetime.utcnow(),
            )
        )
        row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
        if row:
            row.heartbeat_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("append adhoc log failed run=%s", run_id, exc_info=True)
    finally:
        db.close()


def _materialize_log_snapshot(run_id: int) -> None:
    db = SessionLocal()
    try:
        chunks = (
            db.query(AdhocRunLogChunk.content)
            .filter(AdhocRunLogChunk.run_id == run_id)
            .order_by(AdhocRunLogChunk.seq)
            .all()
        )
        content = "".join(item[0] or "" for item in chunks)
        row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
        if not row:
            return
        row.log_content = content
        if row.node_instance_id:
            inst = db.query(NodeInstance).filter(NodeInstance.id == row.node_instance_id).first()
            if inst:
                inst.log_content = content
        db.commit()
    finally:
        db.close()


def request_cancel(db: Any, row: AdhocRun) -> AdhocRun:
    if row.status in TERMINAL_STATUSES:
        return row
    now = datetime.utcnow()
    if row.status == "queued":
        row.status = "cancelled"
        row.finished_at = now
        row.execution_key = None
        if row.node_instance_id:
            inst = db.query(NodeInstance).filter(NodeInstance.id == row.node_instance_id).first()
            if inst:
                inst.status = "cancelled"
                inst.finished_at = now
    else:
        row.status = "cancel_requested"
    row.cancel_requested_at = now
    db.commit()
    with _active_lock:
        proc = _active_processes.get(row.id)
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()
    db.refresh(row)
    return row


def _is_cancelled(run_id: int) -> bool:
    db = SessionLocal()
    try:
        row = db.query(AdhocRun.status).filter(AdhocRun.id == run_id).first()
        return bool(row and row[0] == "cancel_requested")
    finally:
        db.close()


def _heartbeat(run_id: int) -> None:
    db = SessionLocal()
    try:
        db.query(AdhocRun).filter(AdhocRun.id == run_id).update(
            {AdhocRun.heartbeat_at: datetime.utcnow()}, synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _heartbeat_loop(run_id: int, stop_event: threading.Event) -> None:
    while not stop_event.wait(5):
        try:
            _heartbeat(run_id)
        except Exception:
            logger.warning("adhoc heartbeat failed run=%s", run_id, exc_info=True)


def _stream_process(
    run_id: int,
    command: list[str],
    *,
    env: Optional[dict] = None,
    timeout: int,
) -> list[str]:
    lines: list[str] = []
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    with _active_lock:
        _active_processes[run_id] = proc
    try:
        assert proc.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        last_control_check = 0.0
        last_flush = time.monotonic()
        pending: list[str] = []

        def flush_pending() -> None:
            nonlocal last_flush
            if pending:
                append_log(run_id, "".join(pending))
                pending.clear()
            last_flush = time.monotonic()

        while True:
            events = selector.select(timeout=0.25)
            for key, _ in events:
                line = key.fileobj.readline()
                if line:
                    clean = line.rstrip("\n")
                    lines.append(clean)
                    pending.append(clean + "\n")
                    if sum(len(item) for item in pending) >= 16 * 1024:
                        flush_pending()
            if pending and time.monotonic() - last_flush >= 0.5:
                flush_pending()
            if proc.poll() is not None:
                remainder = proc.stdout.read()
                if remainder:
                    for line in remainder.splitlines():
                        lines.append(line)
                        pending.append(line + "\n")
                flush_pending()
                break
            now = time.monotonic()
            if now - last_control_check >= 1:
                last_control_check = now
                _heartbeat(run_id)
                if _is_cancelled(run_id):
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        proc.terminate()
                    raise InterruptedError("运行已取消")
                if now - started > timeout:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:
                        proc.kill()
                    raise TimeoutError(f"运行超过 {timeout} 秒")
        if proc.returncode != 0:
            raise RuntimeError(lines[-1] if lines else f"process exit {proc.returncode}")
        return lines
    finally:
        try:
            selector.close()
        except Exception:
            pass
        with _active_lock:
            _active_processes.pop(run_id, None)


def _run_python_streaming(run_id: int, node: Any, db: Any, bizdate: Optional[str]) -> list[str]:
    from app.services.python_job_runner import (
        _PYTHON_JOB_LIB,
        _macro_context,
        _write_context_file,
        datasource_to_job_context,
    )
    from app.services.business_date import normalize_business_date
    from app.services.workspace_datasource_policy import load_datasource_for_run, resolve_datasource_id
    from app.services.workspace_variables import substitute_script_variables

    biz = normalize_business_date(bizdate)
    ctx = _macro_context(db, node, bizdate=biz)
    ds_id = resolve_datasource_id(
        db, workspace_id=node.workspace_id, explicit_datasource_id=node.datasource_id
    )
    if ds_id:
        ds = load_datasource_for_run(
            db,
            workspace_id=node.workspace_id,
            explicit_datasource_id=node.datasource_id,
            role="PYTHON 节点数据源",
        )
        ctx.update(datasource_to_job_context(ds))
        append_log(run_id, f"[INFO] 已注入数据源「{ds.name}」({ds.ds_type})\n")
    else:
        append_log(run_id, "[WARN] 未配置运行数据源；job.execute 将不可用\n")
    script = substitute_script_variables(
        db,
        int(node.workspace_id),
        node.script_content or "",
        "batch",
        bizdate=bizdate,
        extra_vars=ctx.get("variables"),
    )
    ctx_path = _write_context_file(ctx)
    sensitive_names = ("key", "token", "secret", "password", "credential")
    with _active_lock:
        _run_secrets[run_id] = tuple(
            str(value)
            for name, value in (ctx.get("variables") or {}).items()
            if value and any(part in str(name).lower() for part in sensitive_names)
        )
    script_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(script)
            script_path = handle.name
        env = os.environ.copy()
        pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = _PYTHON_JOB_LIB + (os.pathsep + pp if pp else "")
        env["GIDO_JOB_CONTEXT_FILE"] = ctx_path
        return _stream_process(
            run_id,
            ["python3", script_path],
            env=env,
            timeout=max(1, int(node.timeout_seconds or 300)),
        )
    finally:
        for path in (script_path, ctx_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        with _active_lock:
            _run_secrets.pop(run_id, None)


def _run_shell_streaming(run_id: int, script: str, timeout: int) -> list[str]:
    path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as handle:
            handle.write(script)
            path = handle.name
        return _stream_process(run_id, ["bash", path], timeout=timeout)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _run_probe(row: AdhocRun, db: Any) -> Dict[str, Any]:
    from app.api.probe import _execute_one
    from app.models.workspace import DataSource
    from app.services.sql_readonly import parse_readonly_statements
    from app.services.workspace_variables import substitute_script_variables

    ds = db.query(DataSource).filter(DataSource.id == row.datasource_id).first()
    if not ds:
        raise RuntimeError("数据源不存在")
    sql = substitute_script_variables(db, row.workspace_id, row.sql_text or "", "batch")
    statements = parse_readonly_statements(sql)
    limit = min(max(int((row.request_payload or {}).get("limit") or 1000), 1), 10000)
    results = []
    started = time.monotonic()
    for index, stmt in enumerate(statements):
        if _is_cancelled(row.id):
            raise InterruptedError("运行已取消")
        if time.monotonic() - started > 300:
            raise TimeoutError("数据探查运行超过 300 秒")
        append_log(row.id, f"[INFO] 执行语句 {index + 1}/{len(statements)}\n")
        try:
            block = _execute_one(ds, stmt, limit)
            if _is_cancelled(row.id):
                raise InterruptedError("运行已取消")
            if time.monotonic() - started > 300:
                raise TimeoutError("数据探查运行超过 300 秒")
            block.update({"index": index, "error": None})
            append_log(
                row.id,
                f"[INFO] 语句 {index + 1} 完成，返回 {int(block.get('total') or 0)} 行\n",
            )
        except (InterruptedError, TimeoutError):
            raise
        except Exception as exc:
            append_log(row.id, f"[ERROR] 语句 {index + 1} 失败: {exc}\n", "stderr")
            block = {
                "index": index,
                "sql": stmt,
                "columns": [],
                "column_types": [],
                "rows": [],
                "total": 0,
                "truncated": False,
                "error": str(exc)[:2000],
            }
        if block.get("rows"):
            block["rows"] = list(block["rows"])[:200]
            block["truncated"] = bool(block.get("truncated")) or int(block.get("total") or 0) > 200
        results.append(block)
    last_ok = next((item for item in reversed(results) if not item.get("error")), None)
    return {
        "statement_count": len(statements),
        "statements": results,
        "columns": (last_ok or {}).get("columns", []),
        "column_types": (last_ok or {}).get("column_types", []),
        "rows": (last_ok or {}).get("rows", []),
        "total": (last_ok or {}).get("total", 0),
        "truncated": bool((last_ok or {}).get("truncated")),
        "has_errors": any(item.get("error") for item in results),
    }


def _execute_run(run_id: int) -> None:
    db = SessionLocal()
    row = None
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(run_id, heartbeat_stop),
        name=f"adhoc-heartbeat-{run_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
        if not row or row.status != "running":
            return
        append_log(run_id, f"[INFO] 交互式运行 #{run_id} 已启动\n")
        result: Optional[Dict[str, Any]] = None
        logs: list[str] = []
        result_error: Optional[str] = None
        if row.source == "probe":
            result = _run_probe(row, db)
            if result.get("has_errors") and not any(
                not item.get("error") for item in result.get("statements", [])
            ):
                result_error = "所有探查语句均执行失败"
        else:
            node = db.query(TaskNode).filter(TaskNode.id == row.node_id).first()
            if not node:
                raise RuntimeError("节点不存在")
            target = SimpleNamespace(
                id=node.id,
                name=node.name,
                workspace_id=node.workspace_id,
                datasource_id=(row.request_payload or {}).get(
                    "datasource_id", node.datasource_id
                ),
                timeout_seconds=node.timeout_seconds,
                params=(row.request_payload or {}).get("params", node.params),
                node_type=node.node_type,
                script_content=row.sql_text or "",
            )
            kind = (row.run_type or node.node_type or "").upper()
            if kind == "PYTHON":
                logs = _run_python_streaming(run_id, target, db, row.business_date)
            elif kind == "SHELL":
                logs = _run_shell_streaming(
                    run_id, target.script_content, max(1, int(node.timeout_seconds or 300))
                )
            elif kind == "SQL":
                from app.services.studio_sql_run import run_sql_with_result
                from app.api.studio import _resolve_date_expr

                sql_started = time.monotonic()
                sql_timeout = max(1, int(node.timeout_seconds or 300))

                def sql_control() -> None:
                    if _is_cancelled(run_id):
                        raise InterruptedError("运行已取消")
                    if time.monotonic() - sql_started > sql_timeout:
                        raise TimeoutError(f"运行超过 {sql_timeout} 秒")

                logs, result = run_sql_with_result(
                    target,
                    db,
                    row.business_date,
                    resolve_date_expr=_resolve_date_expr,
                    emit=lambda line: append_log(run_id, str(line) + "\n"),
                    control=sql_control,
                )
            elif kind == "SYNC":
                from app.services.integration_node import run_sync_for_node_blocking

                append_log(run_id, "[INFO] 开始执行数据同步\n")
                logs, sync_status, _ = run_sync_for_node_blocking(
                    db,
                    node,
                    trigger_type="studio",
                    timeout_seconds=node.timeout_seconds or 3600,
                )
                for line in logs:
                    append_log(run_id, str(line) + "\n")
                if sync_status != "success":
                    raise RuntimeError("\n".join(logs))
            elif kind == "DEPENDENT":
                from app.services.workflow_dependent import check_dependent_local

                append_log(run_id, "[INFO] 开始检查依赖条件\n")
                ok, logs = check_dependent_local(
                    db, node, business_date=row.business_date
                )
                for line in logs:
                    append_log(run_id, str(line) + "\n")
                if not ok:
                    raise RuntimeError("\n".join(logs))
            elif kind == "VIRTUAL":
                append_log(run_id, "[INFO] 虚拟节点无需执行\n")
            else:
                raise RuntimeError(f"暂不支持异步试跑节点类型 {kind}")

        finished = datetime.utcnow()
        row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
        if not row:
            return
        if row.status == "cancel_requested":
            row.status = "cancelled"
        elif result_error:
            row.status = "failed"
            row.error_message = result_error
        else:
            row.status = "success"
        row.finished_at = finished
        row.heartbeat_at = finished
        row.execution_key = None
        row.result_preview = result if row.source == "probe" else truncate_result_preview(result)
        row.rows_returned = int((result or {}).get("total") or 0)
        if row.started_at:
            row.duration_ms = int((finished - row.started_at).total_seconds() * 1000)
        if row.node_instance_id:
            inst = db.query(NodeInstance).filter(NodeInstance.id == row.node_instance_id).first()
            if inst:
                inst.status = row.status
                inst.finished_at = finished
        db.commit()
        append_log(run_id, f"[INFO] 交互式运行 #{run_id} {row.status}\n")
        _materialize_log_snapshot(run_id)
    except InterruptedError as exc:
        _finish_failed(db, run_id, "cancelled", str(exc))
    except TimeoutError as exc:
        _finish_failed(db, run_id, "timed_out", str(exc))
    except Exception as exc:
        logger.exception("adhoc run failed run=%s", run_id)
        _finish_failed(db, run_id, "failed", str(exc))
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
        db.close()
        with _active_lock:
            _active_threads.pop(run_id, None)


def _finish_failed(db: Any, run_id: int, status: str, error: str) -> None:
    db.rollback()
    row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not row:
        return
    now = datetime.utcnow()
    row.status = status
    row.error_message = error[:4000]
    row.finished_at = now
    row.heartbeat_at = now
    row.execution_key = None
    if row.started_at:
        row.duration_ms = int((now - row.started_at).total_seconds() * 1000)
    if row.node_instance_id:
        inst = db.query(NodeInstance).filter(NodeInstance.id == row.node_instance_id).first()
        if inst:
            inst.status = status
            inst.log_content = error[:10000]
            inst.finished_at = now
    db.commit()
    append_log(run_id, f"[ERROR] {error}\n", "stderr")
    _materialize_log_snapshot(run_id)


def _claim_one() -> Optional[int]:
    db = SessionLocal()
    try:
        query = db.query(AdhocRun).filter(AdhocRun.status == "queued").order_by(AdhocRun.id)
        if db.bind and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        row = query.first()
        if not row:
            return None
        now = datetime.utcnow()
        row.status = "running"
        row.worker_id = _worker_name
        row.started_at = row.started_at or now
        row.heartbeat_at = now
        row.attempt_count = int(row.attempt_count or 0) + 1
        if row.node_instance_id:
            inst = db.query(NodeInstance).filter(NodeInstance.id == row.node_instance_id).first()
            if inst:
                inst.status = "running"
                inst.started_at = inst.started_at or now
        db.commit()
        return row.id
    except Exception:
        db.rollback()
        logger.warning("claim adhoc run failed", exc_info=True)
        return None
    finally:
        db.close()


def reclaim_stale_runs() -> int:
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=2)
        rows = (
            db.query(AdhocRun)
            .filter(
                AdhocRun.status == "running",
                AdhocRun.heartbeat_at.isnot(None),
                AdhocRun.heartbeat_at < cutoff,
            )
            .all()
        )
        for row in rows:
            row.status = "queued"
            row.worker_id = None
            row.heartbeat_at = None
        db.commit()
        return len(rows)
    finally:
        db.close()


def _dispatch_loop() -> None:
    reclaim_stale_runs()
    last_reclaim = time.monotonic()
    while _running:
        if time.monotonic() - last_reclaim >= 30:
            reclaim_stale_runs()
            last_reclaim = time.monotonic()
        with _active_lock:
            capacity = max(1, int(settings.ADHOC_WORKER_CONCURRENCY)) - len(_active_threads)
        for _ in range(max(0, capacity)):
            run_id = _claim_one()
            if not run_id:
                break
            thread = threading.Thread(
                target=_execute_run, args=(run_id,), name=f"adhoc-run-{run_id}", daemon=True
            )
            with _active_lock:
                _active_threads[run_id] = thread
            thread.start()
        time.sleep(0.5)


def start_adhoc_worker() -> None:
    global _dispatcher, _running
    if _running or not settings.ADHOC_ASYNC_ENABLED:
        return
    _running = True
    _dispatcher = threading.Thread(
        target=_dispatch_loop, name="adhoc-run-dispatcher", daemon=True
    )
    _dispatcher.start()


def stop_adhoc_worker() -> None:
    global _running
    _running = False
    if _dispatcher:
        _dispatcher.join(timeout=3)
