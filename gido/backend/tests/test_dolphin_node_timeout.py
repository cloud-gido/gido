# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.dolphin import _node_timeout_minutes, _rewrite_sql_builtins, _strip_sql_comments_for_ds


def test_timeout_minutes_when_seconds_explicit_null():
    # 前端常存 timeout_seconds: null；dict.get 默认值不会生效
    assert _node_timeout_minutes({"timeout_seconds": None}) == 60


def test_timeout_minutes_default_and_custom():
    assert _node_timeout_minutes({}) == 60
    assert _node_timeout_minutes({"timeout_seconds": 7200}) == 120
    assert _node_timeout_minutes({"timeout_seconds": "900"}) == 15


def test_strip_sql_comments_removes_slash_inside_block_comment():
    """Doris DS 驱动会把含「/」的块注释撕烂；发布前应整段去掉。"""
    sql = """
/*******************************************************************************
 * banner
 ******************************************************************************/
USE bigdata_ads;
/* ---------- 用户 / 站点 / 风控维度 ---------- */
INSERT INTO t (a)  -- line comment
SELECT 1 /* 订单数 / 有效 */ AS x
FROM dual WHERE name = 'a/*keep*/b';
"""
    out = _strip_sql_comments_for_ds(sql)
    assert "banner" not in out
    assert "用户 / 站点" not in out
    assert "USE bigdata_ads" in out
    assert "INSERT INTO t" in out
    assert "line comment" not in out
    assert "a/*keep*/b" in out  # 字符串内保留
    # 可执行片段不应再残留独立块注释
    assert "/* ----------" not in out
    assert "/*******************************************************************************" not in out


def test_rewrite_sql_builtins_strips_comments_and_maps_bizdate():
    sql = "/* 用户 / 站点 */ SELECT '${bizdate}' AS d"
    out = _rewrite_sql_builtins(sql)
    assert "*/" not in out
    assert "$[yyyy-MM-dd]" in out
    assert "${bizdate}" not in out
