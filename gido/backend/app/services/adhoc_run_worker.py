# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""持久化交互式运行队列与增量日志 worker。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import selectors
import secrets
import signal
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from sqlalchemy import and_, or_, text as sa_text
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


class LeaseLostError(InterruptedError):
    """The worker no longer owns this run attempt."""


def _lease_filter(run_id: int, lease_token: str):
    return and_(
        AdhocRun.id == run_id,
        AdhocRun.lease_token == lease_token,
        AdhocRun.worker_id == _worker_name,
        AdhocRun.status.in_(("running", "cancel_requested")),
    )


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _node_execution_key(
    node_id: int,
    user_id: int,
    bizdate: Optional[str],
    script: str,
    params: Optional[Dict[str, Any]],
    datasource_id: Optional[int],
    source: str = "studio",
    limit: Optional[int] = None,
) -> str:
    raw = _stable_json(
        {
            "source": source,
            "node_id": node_id,
            "user_id": user_id,
            "business_date": bizdate or "",
            "script_hash": _hash(script),
            "params": params or {},
            "datasource_id": datasource_id,
            "limit": int(limit) if limit is not None else None,
        }
    )
    return _hash(raw)


def _probe_execution_key(
    user_id: int,
    datasource_id: int,
    sql: str,
    object_name: str,
    limit: int,
) -> str:
    raw = _stable_json(
        {
            "source": "probe",
            "user_id": user_id,
            "datasource_id": datasource_id,
            "object_name": object_name,
            "sql_hash": _hash(sql),
            "limit": int(limit),
        }
    )
    return _hash(raw)


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
    source: str = "studio",
    limit: Optional[int] = None,
) -> tuple[AdhocRun, bool]:
    """创建运行与节点实例；同节点、业务日、脚本活动运行只返回已有记录。"""
    from app.core.config import settings

    script = node.script_content or "" if script_content is None else script_content
    effective_datasource_id = (
        datasource_id if datasource_id is not None else node.datasource_id
    )
    cap = max(1, int(settings.ADHOC_RESULT_MAX_ROWS))
    effective_limit = min(max(int(limit or cap), 1), cap)
    execution_key = _node_execution_key(
        node.id,
        user_id,
        bizdate,
        script,
        params,
        effective_datasource_id,
        source,
        effective_limit,
    )
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
        source=source,
        triggered_by=user_id,
        datasource_id=effective_datasource_id,
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
            "limit": effective_limit,
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
    execution_key = _probe_execution_key(
        user_id,
        datasource_id,
        sql,
        object_name,
        limit,
    )
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


def append_log(
    run_id: int,
    content: str,
    stream: str = "stdout",
    lease_token: Optional[str] = None,
) -> None:
    text = _redact_log(run_id, content)
    if not text:
        return
    db = SessionLocal()
    try:
        if db.bind and db.bind.dialect.name == "sqlite":
            db.execute(sa_text("BEGIN IMMEDIATE"))
        query = db.query(AdhocRun).filter(AdhocRun.id == run_id)
        if db.bind and db.bind.dialect.name in ("postgresql", "mysql"):
            query = query.with_for_update()
        row = query.first()
        if not row or (lease_token is not None and row.lease_token != lease_token):
            return
        raw = text.encode("utf-8")
        remaining = max(0, int(settings.ADHOC_LOG_MAX_BYTES) - int(row.log_bytes or 0))
        if not remaining:
            db.rollback()
            return
        clipped = raw[: min(remaining, 64 * 1024)].decode("utf-8", errors="ignore")
        if not clipped:
            db.rollback()
            return
        seq = int(row.log_next_seq or 1)
        db.add(
            AdhocRunLogChunk(
                run_id=run_id,
                seq=seq,
                stream=stream[:16],
                content=clipped,
                created_at=datetime.utcnow(),
            )
        )
        row.log_next_seq = seq + 1
        row.log_bytes = int(row.log_bytes or 0) + len(clipped.encode("utf-8"))
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
    cancelled_while_queued = False
    if row.status == "queued":
        changed = (
            db.query(AdhocRun)
            .filter(AdhocRun.id == row.id, AdhocRun.status == "queued")
            .update(
                {
                    AdhocRun.status: "cancelled",
                    AdhocRun.status_version: AdhocRun.status_version + 1,
                    AdhocRun.finished_at: now,
                    AdhocRun.execution_key: None,
                    AdhocRun.cancel_requested_at: now,
                },
                synchronize_session=False,
            )
        )
        cancelled_while_queued = bool(changed)
        if changed and row.node_instance_id:
            db.query(NodeInstance).filter(NodeInstance.id == row.node_instance_id).update(
                {
                    NodeInstance.status: "cancelled",
                    NodeInstance.finished_at: now,
                },
                synchronize_session=False,
            )
    if not cancelled_while_queued:
        changed = (
            db.query(AdhocRun)
            .filter(
                AdhocRun.id == row.id,
                AdhocRun.status.in_(("running", "cancel_requested")),
            )
            .update(
                {
                    AdhocRun.status: "cancel_requested",
                    AdhocRun.status_version: AdhocRun.status_version + 1,
                    AdhocRun.cancel_requested_at: now,
                },
                synchronize_session=False,
            )
        )
    db.commit()
    db.expire_all()
    row = db.query(AdhocRun).filter(AdhocRun.id == row.id).one()
    if not changed or row.status in TERMINAL_STATUSES:
        return row
    cancel_outcome: Dict[str, Any] = {
        "requested": True,
        "confirmed": row.status == "cancelled",
        "method": "queue_state" if row.status == "cancelled" else "status_only",
        "reason": None if row.status == "cancelled" else "等待执行器确认",
    }
    with _active_lock:
        proc = _active_processes.get(row.id)
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()
        cancel_outcome = {
            "requested": True,
            "confirmed": True,
            "method": "process_signal",
            "reason": None,
        }
    if (
        row.datasource_id
        and row.lease_token
        and ((row.run_type or "").upper() in ("SQL", "PROBE") or row.source == "probe")
    ):
        try:
            from app.models.workspace import DataSource
            from app.services.adhoc_sql_driver import cancel_run_execution

            ds = db.query(DataSource).filter(DataSource.id == row.datasource_id).first()
            if ds:
                outcome = cancel_run_execution(ds, row.id, row.lease_token)
                cancel_outcome = outcome.to_dict()
        except Exception:
            logger.warning("SQL cancel adapter failed run=%s", row.id, exc_info=True)
            cancel_outcome = {
                "requested": True,
                "confirmed": False,
                "method": "control_connection",
                "reason": "取消适配器执行失败，等待租约回收",
            }
    payload = dict(row.request_payload or {})
    payload["cancel_outcome"] = cancel_outcome
    row.request_payload = payload
    db.commit()
    db.refresh(row)
    return row


def _is_cancelled(run_id: int, lease_token: Optional[str] = None) -> bool:
    db = SessionLocal()
    try:
        row = db.query(AdhocRun.status, AdhocRun.lease_token).filter(AdhocRun.id == run_id).first()
        if not row:
            return True
        if lease_token is not None and row[1] != lease_token:
            raise LeaseLostError("运行租约已失效")
        return row[0] == "cancel_requested"
    finally:
        db.close()


def _heartbeat(run_id: int, lease_token: Optional[str] = None) -> None:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        query = db.query(AdhocRun).filter(AdhocRun.id == run_id)
        if lease_token is not None:
            query = query.filter(_lease_filter(run_id, lease_token))
        changed = query.update(
            {
                AdhocRun.heartbeat_at: now,
                AdhocRun.last_progress_at: now,
                AdhocRun.lease_expires_at: now
                + timedelta(seconds=max(5, int(settings.ADHOC_LEASE_SECONDS))),
            },
            synchronize_session=False,
        )
        db.commit()
        if lease_token is not None and not changed:
            raise LeaseLostError("运行租约已失效")
    finally:
        db.close()


def _heartbeat_loop(run_id: int, lease_token: str, stop_event: threading.Event) -> None:
    while not stop_event.wait(5):
        try:
            _heartbeat(run_id, lease_token)
        except LeaseLostError:
            return
        except Exception:
            logger.warning("adhoc heartbeat failed run=%s", run_id, exc_info=True)


def _stream_process(
    run_id: int,
    command: list[str],
    *,
    env: Optional[dict] = None,
    timeout: int,
    lease_token: Optional[str] = None,
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
                if lease_token is None:
                    append_log(run_id, "".join(pending))
                else:
                    append_log(run_id, "".join(pending), lease_token=lease_token)
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
                if lease_token is None:
                    _heartbeat(run_id)
                else:
                    _heartbeat(run_id, lease_token)
                cancelled = (
                    _is_cancelled(run_id)
                    if lease_token is None
                    else _is_cancelled(run_id, lease_token)
                )
                if cancelled:
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


def _run_python_streaming(
    run_id: int, lease_token: str, node: Any, db: Any, bizdate: Optional[str]
) -> list[str]:
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
        append_log(run_id, f"[INFO] 已注入数据源「{ds.name}」({ds.ds_type})\n", lease_token=lease_token)
    else:
        append_log(run_id, "[WARN] 未配置运行数据源；job.execute 将不可用\n", lease_token=lease_token)
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
            lease_token=lease_token,
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


def _run_shell_streaming(
    run_id: int, lease_token: str, script: str, timeout: int
) -> list[str]:
    path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as handle:
            handle.write(script)
            path = handle.name
        return _stream_process(
            run_id, ["bash", path], timeout=timeout, lease_token=lease_token
        )
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _run_probe(row: AdhocRun, lease_token: str, db: Any) -> Dict[str, Any]:
    from app.models.workspace import DataSource
    from app.services.adhoc_sql_driver import (
        ExecutionBinding,
        connection_query_id,
        is_server_timeout_error,
        registered_connection,
        statement_handle,
        tagged_sql,
    )
    from app.services.adhoc_run_store import (
        append_statement_result_chunk,
        build_result_preview_from_statements,
        complete_run_statement_failed,
        complete_run_statement_success,
        initialize_run_statements,
        start_run_statement,
    )
    from app.services.sql_readonly import (
        apply_readonly_row_limit,
        column_fields_from_description,
        column_types_from_description,
        field_packets_from_cursor,
        json_cell_value,
        parse_readonly_statements,
    )
    from app.services.result_fetch import fetch_result_batch
    from app.services.workspace_variables import substitute_script_variables

    ds = db.query(DataSource).filter(DataSource.id == row.datasource_id).first()
    if not ds:
        raise RuntimeError("数据源不存在")
    sql = substitute_script_variables(db, row.workspace_id, row.sql_text or "", "batch")
    statements = parse_readonly_statements(sql)
    limit = min(
        max(int((row.request_payload or {}).get("limit") or 1000), 1),
        max(1, int(settings.ADHOC_RESULT_MAX_ROWS)),
    )
    first_chunk_rows = max(1, int(settings.ADHOC_RESULT_FIRST_CHUNK_ROWS))
    chunk_rows = max(first_chunk_rows, int(settings.ADHOC_RESULT_CHUNK_ROWS))
    initialize_run_statements(db, row.id, statements, lease_token)
    started = time.monotonic()
    for index, stmt in enumerate(statements):
        if _is_cancelled(row.id, lease_token):
            raise InterruptedError("运行已取消")
        if time.monotonic() - started > 300:
            raise TimeoutError("数据探查运行超过 300 秒")
        append_log(row.id, f"[INFO] 执行语句 {index + 1}/{len(statements)}\n", lease_token=lease_token)
        try:
            statement_started = time.monotonic()
            start_run_statement(db, row.id, index, lease_token)
            binding = ExecutionBinding(row.id, lease_token, index)
            with registered_connection(ds, binding, timeout_seconds=300) as opened:
                conn = opened[1]
                cur = conn.cursor()
                try:
                    with statement_handle(conn, ds.ds_type, binding):
                        execute_started = time.monotonic()
                        cur.execute(
                            tagged_sql(
                                apply_readonly_row_limit(
                                    stmt,
                                    limit,
                                    overflow_probe=True,
                                ),
                                binding,
                                ds.ds_type,
                            )
                        )
                        execute_finished = time.monotonic()
                    columns = [item[0] for item in (cur.description or [])]
                    packets = field_packets_from_cursor(cur)
                    column_types = column_types_from_description(
                        ds.ds_type, cur.description or [], packets
                    )
                    fields = column_fields_from_description(
                        ds.ds_type, cur.description or [], packets
                    )
                    query_id = connection_query_id(conn, ds.ds_type)
                    fetched_rows = 0
                    truncated = False
                    preview_rows = []
                    while fetched_rows < limit:
                        if _is_cancelled(row.id, lease_token):
                            raise InterruptedError("运行已取消")
                        batch = fetch_result_batch(
                            cur,
                            fetched_rows=fetched_rows,
                            max_rows=limit,
                            first_chunk_rows=first_chunk_rows,
                            chunk_rows=chunk_rows,
                        )
                        if not batch.rows:
                            break
                        converted = [
                            [json_cell_value(value) for value in item]
                            for item in batch.rows
                        ]
                        persisted = append_statement_result_chunk(
                            db,
                            row.id,
                            index,
                            converted,
                            lease_token,
                            columns=columns,
                            column_types=column_types,
                            fields=fields,
                            truncated=batch.overflow,
                            max_rows=limit,
                            max_bytes=max(1, int(settings.ADHOC_RESULT_MAX_BYTES)),
                        )
                        if persisted is not None and int(persisted.row_count or 0) < len(
                            converted
                        ):
                            converted = converted[: int(persisted.row_count or 0)]
                        preview_rows.extend(converted[: max(0, 200 - len(preview_rows))])
                        fetched_rows += len(converted)
                        if converted and persisted is None:
                            truncated = True
                            break
                        if not converted and batch.rows:
                            truncated = True
                            break
                        if batch.overflow:
                            truncated = True
                            break
                        if batch.exhausted:
                            break
                finally:
                    cur.close()
            if _is_cancelled(row.id, lease_token):
                raise InterruptedError("运行已取消")
            if time.monotonic() - started > 300:
                raise TimeoutError("数据探查运行超过 300 秒")
            statement_finished = time.monotonic()
            complete_run_statement_success(
                db,
                row.id,
                index,
                lease_token,
                columns=columns,
                column_types=column_types,
                fields=fields,
                truncated=truncated,
                query_id=query_id,
                execution_metrics={
                    "execution_ms": max(0, int((execute_finished - execute_started) * 1000)),
                    "fetch_ms": max(0, int((statement_finished - execute_finished) * 1000)),
                    "duration_ms": max(0, int((statement_finished - statement_started) * 1000)),
                    "rows_returned": fetched_rows,
                },
            )
            append_log(
                row.id,
                f"[INFO] 语句 {index + 1} 完成，返回 {fetched_rows} 行\n",
                lease_token=lease_token,
            )
        except (InterruptedError, TimeoutError):
            raise
        except Exception as exc:
            error = (
                "语句执行超过服务端超时限制"
                if is_server_timeout_error(exc)
                else str(exc)
            )
            complete_run_statement_failed(
                db, row.id, index, lease_token, error
            )
            append_log(
                row.id,
                f"[ERROR] 语句 {index + 1} 失败: {error}\n",
                "stderr",
                lease_token,
            )
    return build_result_preview_from_statements(db, row.id) or {
        "statement_count": len(statements),
        "statements": [],
        "columns": [],
        "column_types": [],
        "rows": [],
        "total": 0,
        "truncated": False,
        "has_errors": False,
    }


def _execute_run(run_id: int, lease_token: str) -> None:
    db = SessionLocal()
    row = None
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(run_id, lease_token, heartbeat_stop),
        name=f"adhoc-heartbeat-{run_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        row = db.query(AdhocRun).filter(_lease_filter(run_id, lease_token)).first()
        if not row or row.status != "running":
            return
        append_log(run_id, f"[INFO] 交互式运行 #{run_id} 已启动\n", lease_token=lease_token)
        result: Optional[Dict[str, Any]] = None
        logs: list[str] = []
        result_error: Optional[str] = None
        if row.source == "probe":
            result = _run_probe(row, lease_token, db)
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
                logs = _run_python_streaming(run_id, lease_token, target, db, row.business_date)
            elif kind == "SHELL":
                logs = _run_shell_streaming(
                    run_id,
                    lease_token,
                    target.script_content,
                    max(1, int(node.timeout_seconds or 300)),
                )
            elif kind == "SQL":
                from app.services.studio_sql_run import run_sql_with_result
                from app.api.studio import _resolve_date_expr
                from app.services.adhoc_sql_driver import ExecutionBinding

                sql_started = time.monotonic()
                sql_timeout = max(1, int(node.timeout_seconds or 300))

                def sql_control() -> None:
                    if _is_cancelled(run_id, lease_token):
                        raise InterruptedError("运行已取消")
                    if time.monotonic() - sql_started > sql_timeout:
                        raise TimeoutError(f"运行超过 {sql_timeout} 秒")

                logs, result = run_sql_with_result(
                    target,
                    db,
                    row.business_date,
                    resolve_date_expr=_resolve_date_expr,
                    emit=lambda line: append_log(
                        run_id, str(line) + "\n", lease_token=lease_token
                    ),
                    control=sql_control,
                    execution_binding=ExecutionBinding(run_id, lease_token, 0),
                    timeout_seconds=sql_timeout,
                    run_id=run_id,
                    lease_token=lease_token,
                    max_rows=min(
                        max(int((row.request_payload or {}).get("limit") or settings.ADHOC_RESULT_MAX_ROWS), 1),
                        max(1, int(settings.ADHOC_RESULT_MAX_ROWS)),
                    ),
                )
            elif kind == "SYNC":
                from app.services.integration_node import run_sync_for_node_blocking

                append_log(run_id, "[INFO] 开始执行数据同步\n", lease_token=lease_token)
                logs, sync_status, _ = run_sync_for_node_blocking(
                    db,
                    node,
                    trigger_type="studio",
                    timeout_seconds=node.timeout_seconds or 3600,
                )
                for line in logs:
                    append_log(run_id, str(line) + "\n", lease_token=lease_token)
                if sync_status != "success":
                    raise RuntimeError("\n".join(logs))
            elif kind == "DEPENDENT":
                from app.services.workflow_dependent import check_dependent_local

                append_log(run_id, "[INFO] 开始检查依赖条件\n", lease_token=lease_token)
                ok, logs = check_dependent_local(
                    db, node, business_date=row.business_date
                )
                for line in logs:
                    append_log(run_id, str(line) + "\n", lease_token=lease_token)
                if not ok:
                    raise RuntimeError("\n".join(logs))
            elif kind == "VIRTUAL":
                append_log(run_id, "[INFO] 虚拟节点无需执行\n", lease_token=lease_token)
            else:
                raise RuntimeError(f"暂不支持异步试跑节点类型 {kind}")

        finished = datetime.utcnow()
        db.expire_all()
        query = db.query(AdhocRun).filter(_lease_filter(run_id, lease_token))
        if db.bind and db.bind.dialect.name in ("postgresql", "mysql"):
            query = query.with_for_update()
        row = query.first()
        if not row:
            raise LeaseLostError("运行租约已失效")
        if row.status == "cancel_requested":
            row.status = "cancelled"
        elif result_error:
            row.status = "failed"
            row.error_message = result_error
        else:
            row.status = "success"
        row.finished_at = finished
        row.heartbeat_at = finished
        row.status_version = int(row.status_version or 0) + 1
        row.execution_key = None
        if row.source == "probe" or (row.run_type or "").upper() == "SQL":
            from app.services.adhoc_run_store import build_result_preview_from_statements

            result = build_result_preview_from_statements(db, run_id) or result
        row.result_preview = truncate_result_preview(result)
        row.rows_returned = int((result or {}).get("total") or 0)
        if row.started_at:
            row.duration_ms = int((finished - row.started_at).total_seconds() * 1000)
        if row.node_instance_id:
            inst = db.query(NodeInstance).filter(NodeInstance.id == row.node_instance_id).first()
            if inst:
                inst.status = row.status
                inst.finished_at = finished
        terminal_status = row.status
        row.lease_token = None
        row.lease_expires_at = None
        db.commit()
        # Commit the terminal state before opening append_log's independent
        # Session; otherwise both Sessions can wait on the same run row.
        append_log(run_id, f"[INFO] 交互式运行 #{run_id} {terminal_status}\n")
        _materialize_log_snapshot(run_id)
    except InterruptedError as exc:
        if not isinstance(exc, LeaseLostError):
            _finish_failed(db, run_id, lease_token, "cancelled", str(exc))
    except TimeoutError as exc:
        _finish_failed(db, run_id, lease_token, "timed_out", str(exc))
    except Exception as exc:
        logger.exception("adhoc run failed run=%s", run_id)
        _finish_failed(db, run_id, lease_token, "failed", str(exc))
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
        db.close()
        with _active_lock:
            _active_threads.pop(run_id, None)


def _finish_failed(
    db: Any, run_id: int, lease_token: str, status: str, error: str
) -> None:
    db.rollback()
    query = db.query(AdhocRun).filter(_lease_filter(run_id, lease_token))
    if db.bind and db.bind.dialect.name in ("postgresql", "mysql"):
        query = query.with_for_update()
    row = query.first()
    if not row:
        return
    now = datetime.utcnow()
    row.status = status
    row.error_message = error[:4000]
    row.finished_at = now
    row.heartbeat_at = now
    row.execution_key = None
    row.status_version = int(row.status_version or 0) + 1
    if row.source == "probe" or (row.run_type or "").upper() == "SQL":
        from app.services.adhoc_run_store import build_result_preview_from_statements

        preview = build_result_preview_from_statements(db, run_id)
        if preview:
            row.result_preview = truncate_result_preview(preview)
            row.rows_returned = int(preview.get("total") or 0)
    if row.started_at:
        row.duration_ms = int((now - row.started_at).total_seconds() * 1000)
    if row.node_instance_id:
        inst = db.query(NodeInstance).filter(NodeInstance.id == row.node_instance_id).first()
        if inst:
            inst.status = status
            inst.log_content = error[:10000]
            inst.finished_at = now
    row.lease_token = None
    row.lease_expires_at = None
    db.commit()
    # See the success path: do not append through a second Session while this
    # transaction owns/has flushed the run row.
    append_log(run_id, f"[ERROR] {error}\n", "stderr")
    _materialize_log_snapshot(run_id)


def _under_quota(db: Any, row: AdhocRun) -> bool:
    active = ("running", "cancel_requested")
    scopes = (
        (int(settings.ADHOC_CONCURRENCY_GLOBAL), ()),
        (
            int(settings.ADHOC_CONCURRENCY_PER_WORKSPACE),
            (AdhocRun.workspace_id == row.workspace_id,),
        ),
        (
            int(settings.ADHOC_CONCURRENCY_PER_USER),
            (AdhocRun.triggered_by == row.triggered_by,),
        ),
        (
            int(settings.ADHOC_CONCURRENCY_PER_DATASOURCE),
            (AdhocRun.datasource_id == row.datasource_id,),
        ),
    )
    for limit, filters in scopes:
        if limit <= 0:
            continue
        query = db.query(AdhocRun.id).filter(AdhocRun.status.in_(active), *filters)
        if query.count() >= limit:
            return False
    return True


def _claim_one() -> Optional[tuple[int, str]]:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        db.query(AdhocRun).filter(
            AdhocRun.status == "queued",
            AdhocRun.queue_deadline_at.isnot(None),
            AdhocRun.queue_deadline_at < now,
        ).update(
            {
                AdhocRun.status: "timed_out",
                AdhocRun.finished_at: now,
                AdhocRun.execution_key: None,
                AdhocRun.status_version: AdhocRun.status_version + 1,
            },
            synchronize_session=False,
        )
        query = (
            db.query(AdhocRun)
            .filter(AdhocRun.status == "queued")
            .order_by(AdhocRun.queue_deadline_at, AdhocRun.created_at, AdhocRun.id)
        )
        if db.bind and db.bind.dialect.name in ("postgresql", "mysql"):
            query = query.with_for_update(skip_locked=True)
        row = next((candidate for candidate in query.limit(100) if _under_quota(db, candidate)), None)
        if not row:
            db.commit()
            return None
        token = secrets.token_hex(24)
        row.status = "running"
        row.status_version = int(row.status_version or 0) + 1
        row.worker_id = _worker_name
        row.lease_token = token
        row.lease_expires_at = now + timedelta(
            seconds=max(5, int(settings.ADHOC_LEASE_SECONDS))
        )
        row.claimed_at = now
        row.started_at = row.started_at or now
        row.heartbeat_at = now
        row.last_progress_at = now
        row.attempt_count = int(row.attempt_count or 0) + 1
        if row.node_instance_id:
            inst = db.query(NodeInstance).filter(NodeInstance.id == row.node_instance_id).first()
            if inst:
                inst.status = "running"
                inst.started_at = inst.started_at or now
        db.commit()
        return row.id, token
    except Exception:
        db.rollback()
        logger.warning("claim adhoc run failed", exc_info=True)
        return None
    finally:
        db.close()


def reclaim_stale_runs() -> int:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        query = (
            db.query(AdhocRun)
            .filter(
                AdhocRun.status.in_(("running", "cancel_requested")),
                or_(
                    AdhocRun.lease_expires_at < now,
                    and_(
                        AdhocRun.lease_expires_at.is_(None),
                        AdhocRun.heartbeat_at
                        < now
                        - timedelta(seconds=max(5, int(settings.ADHOC_LEASE_SECONDS))),
                    ),
                ),
            )
        )
        if db.bind and db.bind.dialect.name in ("postgresql", "mysql"):
            query = query.with_for_update(skip_locked=True)
        rows = query.all()
        for row in rows:
            payload = row.request_payload if isinstance(row.request_payload, dict) else {}
            retryable = (
                row.source == "probe"
                or payload.get("read_only") is True
                or not row.lease_token  # legacy pre-lease row; preserve old recovery behavior
            )
            if row.status == "cancel_requested":
                row.status = "cancelled"
                row.finished_at = now
                row.execution_key = None
            elif retryable:
                row.status = "queued"
            else:
                row.status = "failed"
                row.error_message = "worker lost；非幂等交互式运行未自动重试"
                row.finished_at = now
                row.execution_key = None
                if row.node_instance_id:
                    inst = (
                        db.query(NodeInstance)
                        .filter(NodeInstance.id == row.node_instance_id)
                        .first()
                    )
                    if inst:
                        inst.status = "failed"
                        inst.finished_at = now
            row.worker_id = None
            row.lease_token = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            row.status_version = int(row.status_version or 0) + 1
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
            claimed = _claim_one()
            if not claimed:
                break
            run_id, lease_token = claimed
            thread = threading.Thread(
                target=_execute_run,
                args=(run_id, lease_token),
                name=f"adhoc-run-{run_id}",
                daemon=True,
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
    deadline = time.monotonic() + max(0, int(settings.ADHOC_DRAIN_SECONDS))
    while time.monotonic() < deadline:
        with _active_lock:
            threads = list(_active_threads.values())
        if not threads:
            return
        for thread in threads:
            thread.join(timeout=min(0.2, max(0.0, deadline - time.monotonic())))
