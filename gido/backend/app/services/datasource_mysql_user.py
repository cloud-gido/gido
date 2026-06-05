# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""MySQL 协议连接（Doris FE / MySQL）用户名：与 Dolphin JDBC、PyMySQL 行为对齐。"""


def mysql_protocol_connect_user(ds) -> str:
    """
    GIDO 里 Doris 可无显式账号（FE 未开认证时等价于 root）。
    DolphinScheduler MySQL 数据源若 userName 为空，JDBC 会走 anonym@null 导致 Access denied。
    故：doris 且用户名为空时，对下游统一用 root；其它类型仍用填写值（可为空串）。
    """
    raw = (getattr(ds, "username", None) or "").strip()
    if raw:
        return raw
    if str(getattr(ds, "ds_type", "") or "").lower() == "doris":
        return "root"
    return ""
