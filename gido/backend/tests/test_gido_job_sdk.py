# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""gido_job SDK + PYTHON 节点 runner 基础测试。"""
from __future__ import annotations

import json
import os
import sys
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
