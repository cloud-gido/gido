# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""PYTHON 节点运行：注入 gido_job SDK 与数据源上下文。"""
from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.services.datasource_mysql_user import mysql_protocol_connect_user

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.workspace import DataSource, TaskNode

logger = logging.getLogger(__name__)

# gido/backend/python_job_lib （与 app 同级）
_PYTHON_JOB_LIB = str((Path(__file__).resolve().parents[2] / "python_job_lib").resolve())


def datasource_to_job_context(ds: Any) -> Dict[str, Any]:
    return {
        "datasource_id": ds.id,
        "name": ds.name,
        "ds_type": (ds.ds_type or "").strip().lower(),
        "host": ds.host or "",
        "port": ds.port,
        "database": ds.database or "",
        "username": mysql_protocol_connect_user(ds),
        "password": ds.password or "",
    }


def _write_context_file(ctx: Dict[str, Any]) -> str:
    fd, path = tempfile.mkstemp(prefix="gido-job-ctx-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ctx, f, ensure_ascii=False)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def run_python_node(node: Any, db: Any, *, timeout_seconds: Optional[int] = None) -> List[str]:
    """执行 PYTHON 节点脚本；返回日志行列表。"""
    from app.services.workspace_datasource_policy import load_datasource_for_run, resolve_datasource_id

    timeout = timeout_seconds or node.timeout_seconds or 300
    if timeout < 1:
        timeout = 300

    ctx_path: Optional[str] = None
    script_path: Optional[str] = None
    logs: List[str] = []

    ds_id = resolve_datasource_id(
        db,
        workspace_id=node.workspace_id,
        explicit_datasource_id=node.datasource_id,
    )
    if ds_id:
        try:
            ds = load_datasource_for_run(
                db,
                workspace_id=node.workspace_id,
                explicit_datasource_id=node.datasource_id,
                role="PYTHON 节点数据源",
            )
            ctx_path = _write_context_file(datasource_to_job_context(ds))
            logs.append(f"[INFO] 已注入数据源「{ds.name}」({ds.ds_type}) 供 gido_job.execute 使用")
        except Exception as e:
            logger.warning("PYTHON 节点数据源注入跳过: %s", e)
            logs.append(f"[WARN] 数据源未注入: {e}（仅 writelog/print 可用；execute 将失败）")
    else:
        logs.append(
            "[WARN] 未配置节点数据源且无空间默认；job.execute 将失败。"
            "请在节点配置或空间设置中指定数据源。"
        )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(node.script_content or "")
        script_path = f.name

    env = os.environ.copy()
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _PYTHON_JOB_LIB + (os.pathsep + pp if pp else "")
    if ctx_path:
        env["GIDO_JOB_CONTEXT_FILE"] = ctx_path

    try:
        result = subprocess.run(
            ["python3", script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.stdout:
            logs.append(result.stdout.rstrip("\n"))
        if result.returncode != 0:
            err = (result.stderr or "").strip() or f"python3 exit {result.returncode}"
            raise RuntimeError(err)
    finally:
        if script_path:
            try:
                os.unlink(script_path)
            except OSError:
                pass
        if ctx_path:
            try:
                os.unlink(ctx_path)
            except OSError:
                pass

    return logs
