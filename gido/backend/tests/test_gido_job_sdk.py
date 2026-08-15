# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""gido_job SDK + PYTHON 节点 runner 基础测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_JOB_LIB = str(_BACKEND_ROOT / "python_job_lib")
if _JOB_LIB not in sys.path:
    sys.path.insert(0, _JOB_LIB)

from gido_job.context import ENV_CONTEXT_FILE, load_job_context, mysql_protocol_user
from gido_job.job import GidoJob
from gido_job.macros import resolve_date_expr, substitute_sql_macros
from app.services.python_job_runner import _PYTHON_JOB_LIB, datasource_to_job_context


def test_mysql_protocol_user_doris_root():
    assert mysql_protocol_user("doris", "") == "root"
    assert mysql_protocol_user("doris", None) == "root"
    assert mysql_protocol_user("mysql", "") == ""


def test_load_job_context_from_env_file(tmp_path, monkeypatch):
    p = tmp_path / "ctx.json"
    p.write_text(json.dumps({"ds_type": "doris", "host": "h1"}), encoding="utf-8")
    monkeypatch.setenv(ENV_CONTEXT_FILE, str(p))
    ctx = load_job_context()
    assert ctx["host"] == "h1"
    assert ctx["ds_type"] == "doris"


def test_load_job_context_missing_returns_none(monkeypatch):
    monkeypatch.delenv(ENV_CONTEXT_FILE, raising=False)
    assert load_job_context() is None


def test_writelog_prints(capsys):
    j = GidoJob()
    j.writelog("hello", 123)
    assert "hello 123" in capsys.readouterr().out


def test_execute_requires_context(monkeypatch):
    monkeypatch.delenv(ENV_CONTEXT_FILE, raising=False)
    j = GidoJob()
    with pytest.raises(RuntimeError, match="未注入数据源"):
        j.execute("SELECT 1")


def test_execute_mysql_select(monkeypatch, tmp_path):
    p = tmp_path / "ctx.json"
    p.write_text(
        json.dumps(
            {
                "ds_type": "doris",
                "host": "fe",
                "port": 9030,
                "username": "",
                "password": "x",
                "database": "demo",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_CONTEXT_FILE, str(p))

    mock_cur = MagicMock()
    mock_cur.description = (("n",),)
    mock_cur.fetchall.return_value = [{"n": 1}]
    mock_cur.rowcount = 1
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    fake_pymysql = MagicMock()
    fake_pymysql.connect.return_value = mock_conn
    fake_cursors = MagicMock()
    fake_cursors.DictCursor = object
    monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
    monkeypatch.setitem(sys.modules, "pymysql.cursors", fake_cursors)

    j = GidoJob()
    rows = j.execute("SELECT 1 AS n")
    assert rows == [{"n": 1}]
    kwargs = fake_pymysql.connect.call_args.kwargs
    assert kwargs["user"] == "root"
    assert kwargs["port"] == 9030


def test_datasource_to_job_context_doris():
    ds = SimpleNamespace(
        id=9,
        name="d1",
        ds_type="doris",
        host="h",
        port=9030,
        database="db",
        username="",
        password="p",
    )
    ctx = datasource_to_job_context(ds)
    assert ctx["username"] == "root"
    assert ctx["datasource_id"] == 9


def test_python_job_lib_path_exists():
    assert Path(_PYTHON_JOB_LIB).is_dir()
    assert (Path(_PYTHON_JOB_LIB) / "gido_job" / "__init__.py").is_file()


def test_subprocess_can_import_gido_job():
    """子进程 PYTHONPATH 注入后可 import gido_job 并 writelog。"""
    import subprocess

    env = os.environ.copy()
    env["PYTHONPATH"] = _PYTHON_JOB_LIB + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-c", "from gido_job import job; job.writelog('ok-from-sdk')"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "ok-from-sdk" in result.stdout


def test_resolve_date_expr_offset_with_space():
    assert resolve_date_expr("$[yyyy-MM-dd -3]", "2026-07-17") == "2026-07-14"
    assert resolve_date_expr("$[yyyy-MM-dd-3]", "2026-07-17") == "2026-07-14"


def test_resolve_date_expr_dolphin_hour_minute_second():
    """Dolphin ±N/24 时、±N/24/60 分、±N/24/60/60 秒。"""
    assert resolve_date_expr("$[yyyy-MM-dd-1/24]", "2026-08-15") == "2026-08-14"
    assert resolve_date_expr("$[yyyy-MM-dd-8/24]", "2026-08-15") == "2026-08-14"
    assert resolve_date_expr("$[yyyy-MM-dd+1/24]", "2026-08-15") == "2026-08-15"
    assert resolve_date_expr("$[yyyy-MM-dd -1/24]", "2026-08-15") == "2026-08-14"
    assert resolve_date_expr("$[yyyy-MM-dd-1/24/60]", "2026-08-15") == "2026-08-14"
    assert resolve_date_expr("$[yyyyMMdd-1/24]", "2026-08-15") == "20260814"
    out = substitute_sql_macros(
        "d='$[yyyy-MM-dd-1/24]'",
        bizdate="2026-08-15",
        tz_name="Asia/Shanghai",
    )
    assert out == "d='2026-08-14'"
    curly = substitute_sql_macros(
        "d='${yyyy-MM-dd-1/24}'",
        bizdate="2026-08-15",
        tz_name="Asia/Shanghai",
    )
    assert curly == "d='2026-08-14'"


def test_substitute_sql_macros_bizdate_and_bracket():
    sql = (
        "select count(distinct if(first_login_date='$[yyyy-MM-dd -3]',did,null)) "
        "from t where d='${bizdate}'"
    )
    out = substitute_sql_macros(sql, bizdate="2026-07-17", tz_name="Asia/Shanghai")
    assert "'2026-07-14'" in out
    assert "'2026-07-17'" in out
    assert "$[" not in out
    assert "${bizdate}" not in out


def test_substitute_sql_macros_never_raises():
    # 畸形宏保留原文，不抛错
    assert substitute_sql_macros("select '$[not-a-valid'") == "select '$[not-a-valid'"


def test_job_var_reads_variables(monkeypatch, tmp_path):
    p = tmp_path / "ctx.json"
    p.write_text(
        json.dumps(
            {
                "ds_type": "doris",
                "host": "fe",
                "variables": {"LARK_WEBHOOK_URL": "https://hooks.example/x"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_CONTEXT_FILE, str(p))
    j = GidoJob()
    assert j.var("LARK_WEBHOOK_URL") == "https://hooks.example/x"
    assert j.var("missing", default="fallback") == "fallback"
    with pytest.raises(KeyError, match="未找到全局变量"):
        j.var("missing")
    with pytest.raises(ValueError, match="不能为空"):
        j.var("  ")


def test_job_var_requires_context(monkeypatch):
    monkeypatch.delenv(ENV_CONTEXT_FILE, raising=False)
    j = GidoJob()
    with pytest.raises(RuntimeError, match="未注入数据源"):
        j.var("any")


def test_run_python_node_substitutes_source_vars(monkeypatch, tmp_path):
    """PYTHON 源码中的 ${key} 在落盘执行前展开（与 SQL 一致）。"""
    from app.services import python_job_runner as pjr
    import app.services.workspace_variables as wv

    written: dict = {}
    real_ntf = tempfile.NamedTemporaryFile

    def tracking_ntf(*args, **kwargs):
        kwargs.setdefault("delete", False)
        tmp = real_ntf(*args, **kwargs)
        orig_write = tmp.write

        def write(data):
            written["script"] = data if isinstance(data, str) else data.decode("utf-8")
            return orig_write(data)

        tmp.write = write  # type: ignore[method-assign]
        return tmp

    monkeypatch.setattr(pjr.tempfile, "NamedTemporaryFile", tracking_ntf)

    def fake_sub(db, workspace_id, script, scope, bizdate=None, extra_vars=None):
        return (script or "").replace("${my_webhook}", "https://hooks.example/ok")

    monkeypatch.setattr(wv, "substitute_script_variables", fake_sub)
    monkeypatch.setattr(pjr, "_write_context_file", lambda ctx: str(tmp_path / "ctx.json"))
    (tmp_path / "ctx.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        pjr,
        "_macro_context",
        lambda db, node, bizdate=None: {
            "timezone": "Asia/Shanghai",
            "bizdate": "2026-08-01",
            "yesterday": "2026-07-31",
            "variables": {"my_webhook": "https://hooks.example/ok"},
        },
    )
    monkeypatch.setattr(
        "app.services.workspace_datasource_policy.resolve_datasource_id",
        lambda *a, **k: None,
    )

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="ran\n", stderr="")

    monkeypatch.setattr(pjr.subprocess, "run", fake_run)

    node = SimpleNamespace(
        workspace_id=1,
        datasource_id=None,
        timeout_seconds=30,
        script_content='webhook = "${my_webhook}"\nprint(webhook)\n',
        params={},
    )
    logs = pjr.run_python_node(node, db=MagicMock(), bizdate="2026-08-01")
    assert "https://hooks.example/ok" in written["script"]
    assert "${my_webhook}" not in written["script"]
    assert any("ran" in (x or "") for x in logs)


def test_execute_macros_still_work_after_source_sub(monkeypatch, tmp_path):
    """源码已无 ${} 时，execute 内 $[...] 仍展开。"""
    p = tmp_path / "ctx.json"
    p.write_text(
        json.dumps(
            {
                "ds_type": "mysql",
                "host": "h",
                "port": 3306,
                "username": "u",
                "password": "p",
                "database": "db",
                "bizdate": "2026-07-17",
                "timezone": "Asia/Shanghai",
                "variables": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_CONTEXT_FILE, str(p))

    mock_cur = MagicMock()
    mock_cur.description = (("d",),)
    mock_cur.fetchall.return_value = [{"d": "2026-07-14"}]
    mock_cur.rowcount = 1
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    fake_pymysql = MagicMock()
    fake_pymysql.connect.return_value = mock_conn
    fake_cursors = MagicMock()
    fake_cursors.DictCursor = object
    monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
    monkeypatch.setitem(sys.modules, "pymysql.cursors", fake_cursors)

    j = GidoJob()
    j.execute("SELECT '$[yyyy-MM-dd-3]' AS d")
    sql_arg = mock_cur.execute.call_args[0][0]
    assert "2026-07-14" in sql_arg
    assert "$[" not in sql_arg
