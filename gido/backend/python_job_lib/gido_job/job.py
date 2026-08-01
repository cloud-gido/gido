# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""GidoJob：execute / writelog。"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Sequence, Union

from gido_job.context import load_job_context, mysql_protocol_user
from gido_job.macros import substitute_sql_macros


class GidoJob:
    """PYTHON 节点作业上下文。数据源由节点绑定（或空间默认）注入。"""

    def __init__(self) -> None:
        self.last_rowcount: int = -1
        self._ctx: Optional[Dict[str, Any]] = None

    def writelog(self, *args: Any, sep: str = " ", **_kwargs: Any) -> None:
        """写入节点运行日志（stdout，由 GIDO 捕获）。"""
        text = sep.join(str(a) for a in args)
        print(text, flush=True)

    def var(self, key: str, default: Optional[str] = None) -> str:
        """读取空间全局变量 / 节点 params（与脚本 ``${key}`` 同源）。

        - 优先用 SDK 读取含引号、多行等复杂值，避免源码 ``${key}`` 朴素替换踩坑
        - ``key`` 缺失且未给 ``default`` 时抛 ``KeyError``，提示去空间设置配置
        """
        name = (key or "").strip()
        if not name:
            raise ValueError("job.var(key) 的 key 不能为空")
        ctx = self._ensure_context()
        variables = ctx.get("variables") if isinstance(ctx.get("variables"), dict) else {}
        if name in variables:
            raw = variables[name]
            return "" if raw is None else str(raw)
        if default is not None:
            return str(default)
        raise KeyError(
            f"未找到全局变量「{name}」：请在「空间设置 → 全局变量」配置，"
            f"或在节点 params 中提供；也可写 job.var({name!r}, default='...')"
        )

    def execute(
        self,
        sql: str,
        params: Optional[Union[Sequence[Any], Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """对绑定数据源执行 SQL。

        - 自动展开 ``$[yyyy-MM-dd-1]`` / ``${bizdate}`` / 空间变量（与 SQL 节点一致）
        - SELECT / 有结果集：返回 ``list[dict]``
        - DML / DDL：返回 ``[]``，影响行数写入 ``last_rowcount`` 并 writelog
        """
        stmt = (sql or "").strip()
        if not stmt:
            raise ValueError("execute(sql) 的 sql 不能为空")

        ctx = self._ensure_context()
        stmt = substitute_sql_macros(
            stmt,
            bizdate=ctx.get("bizdate"),
            tz_name=str(ctx.get("timezone") or "Asia/Shanghai"),
            variables=ctx.get("variables") if isinstance(ctx.get("variables"), dict) else None,
        )
        ds_type = (ctx.get("ds_type") or "").strip().lower()
        if ds_type in ("mysql", "doris"):
            return self._execute_mysql(ctx, stmt, params)
        if ds_type == "postgresql":
            return self._execute_pg(ctx, stmt, params)
        raise ValueError(
            f"数据源类型 {ctx.get('ds_type')!r} 暂不支持 job.execute，仅支持 mysql / doris / postgresql"
        )

    def _ensure_context(self) -> Dict[str, Any]:
        if self._ctx is not None:
            return self._ctx
        ctx = load_job_context()
        if not ctx:
            raise RuntimeError(
                "未注入数据源上下文：请在 PYTHON 节点「配置」中绑定数据源，"
                "或在「空间设置」配置默认数据源后重试"
            )
        self._ctx = ctx
        return ctx

    def _execute_mysql(
        self,
        ctx: Dict[str, Any],
        sql: str,
        params: Optional[Union[Sequence[Any], Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        import pymysql
        from pymysql.cursors import DictCursor

        ds_type = (ctx.get("ds_type") or "").strip().lower()
        port = ctx.get("port")
        if port is None:
            port = 9030 if ds_type == "doris" else 3306
        conn = pymysql.connect(
            host=ctx.get("host") or "127.0.0.1",
            port=int(port),
            user=mysql_protocol_user(ds_type, ctx.get("username")),
            password=ctx.get("password") or "",
            database=ctx.get("database") or "",
            connect_timeout=15,
            charset="utf8mb4",
            cursorclass=DictCursor,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                self.last_rowcount = cur.rowcount
                if cur.description:
                    rows = list(cur.fetchall())
                    return [{k: _jsonable(v) for k, v in row.items()} for row in rows]
                conn.commit()
                self.writelog(f"[execute] rowcount={self.last_rowcount}")
                return []
        finally:
            conn.close()

    def _execute_pg(
        self,
        ctx: Dict[str, Any],
        sql: str,
        params: Optional[Union[Sequence[Any], Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        import psycopg2
        import psycopg2.extras

        dbname = (ctx.get("database") or "").strip()
        if not dbname:
            raise ValueError("PostgreSQL 数据源未配置数据库名")
        conn = psycopg2.connect(
            host=ctx.get("host") or "127.0.0.1",
            port=int(ctx.get("port") or 5432),
            user=(ctx.get("username") or "").strip() or None,
            password=ctx.get("password") or "",
            dbname=dbname,
            connect_timeout=15,
        )
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                self.last_rowcount = cur.rowcount
                if cur.description:
                    rows = list(cur.fetchall())
                    return [{k: _jsonable(v) for k, v in dict(row).items()} for row in rows]
                conn.commit()
                self.writelog(f"[execute] rowcount={self.last_rowcount}")
                return []
        finally:
            conn.close()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return repr(value)
    return str(value)


# 模块级单例：用户脚本 `from gido_job import job`
job = GidoJob()

# 避免子进程缓冲导致日志延迟（writelog 已 flush；此处兜底）
try:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except Exception:
    pass
