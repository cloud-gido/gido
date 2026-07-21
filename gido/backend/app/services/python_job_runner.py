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
from datetime import datetime
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


def _macro_context(db: Any, node: Any, bizdate: Optional[str] = None) -> Dict[str, Any]:
    """时区 / bizdate / 空间变量 / 节点 params，供 gido_job.execute 宏展开。"""
    from app.core.config import settings
    from app.models.workspace import Workspace
    from app.services.business_date import bizdate_and_yesterday
    from app.services.workspace_variables import load_workspace_variable_map

    ws = db.query(Workspace).filter(Workspace.id == int(node.workspace_id)).first()
    tz_name = (ws.timezone if ws and ws.timezone else None) or getattr(
        settings, "DEFAULT_TIMEZONE", None
    ) or "Asia/Shanghai"
    try:
        import pytz

        now_local = datetime.now(pytz.timezone(tz_name))
    except Exception:
        now_local = datetime.now()

    biz, yesterday = bizdate_and_yesterday(bizdate, now=now_local.replace(tzinfo=None))
    variables: Dict[str, str] = {}
    try:
        variables.update(load_workspace_variable_map(db, int(node.workspace_id), "batch"))
    except Exception as e:
        logger.warning("加载空间变量失败: %s", e)

    params = getattr(node, "params", None) or {}
    if isinstance(params, dict):
        for k, v in params.items():
            if k is None:
                continue
            variables[str(k)] = "" if v is None else str(v)

    return {
        "timezone": tz_name,
        "bizdate": biz,
        "yesterday": yesterday,
        "variables": variables,
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


def run_python_node(
    node: Any,
    db: Any,
    *,
    timeout_seconds: Optional[int] = None,
    bizdate: Optional[str] = None,
) -> List[str]:
    """执行 PYTHON 节点脚本；返回日志行列表。"""
    from app.services.business_date import normalize_business_date
    from app.services.workspace_datasource_policy import load_datasource_for_run, resolve_datasource_id

    timeout = timeout_seconds or node.timeout_seconds or 300
    if timeout < 1:
        timeout = 300
    biz = normalize_business_date(bizdate)

    ctx_path: Optional[str] = None
    script_path: Optional[str] = None
    logs: List[str] = []

    ctx: Dict[str, Any] = {}
    try:
        ctx.update(_macro_context(db, node, bizdate=biz))
    except Exception as e:
        logger.warning("宏上下文构建失败: %s", e)
        ctx.setdefault("timezone", "Asia/Shanghai")
        ctx.setdefault("bizdate", biz or datetime.now().strftime("%Y-%m-%d"))
        ctx.setdefault("variables", {})
    if biz:
        logs.append(f"[INFO] 业务日 bizdate={ctx.get('bizdate')}（宏相对该日展开）")

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
            ctx.update(datasource_to_job_context(ds))
            logs.append(f"[INFO] 已注入数据源「{ds.name}」({ds.ds_type}) 供 gido_job.execute 使用")
        except Exception as e:
            logger.warning("PYTHON 节点数据源注入跳过: %s", e)
            logs.append(f"[WARN] 数据源未注入: {e}（仅 writelog/print 可用；execute 将失败）")
    else:
        logs.append(
            "[WARN] 未配置节点数据源且无空间默认；job.execute 将失败。"
            "请在节点配置或空间设置中指定数据源。"
        )

    ctx_path = _write_context_file(ctx)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(node.script_content or "")
        script_path = f.name

    env = os.environ.copy()
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _PYTHON_JOB_LIB + (os.pathsep + pp if pp else "")
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
            # 失败时保留已写出的 stdout（writelog），避免只剩 [ERROR]
            detail = "\n".join([x for x in logs if x] + [err])
            raise RuntimeError(detail)
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
