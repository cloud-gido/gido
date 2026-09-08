# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""发布到 Dolphin：按 process code 唯一映射，改名不得新建定义。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.dolphin import DSClient


def _client() -> DSClient:
    return DSClient.__new__(DSClient)


# ---------- 单元：绑定解析 ----------


def test_bound_process_code_prefers_scheduler_definition_id():
    c = _client()
    wf = SimpleNamespace(
        scheduler_definition_id="90001",
        dag_config={"ds_process_code": "111"},
    )
    assert c._bound_process_code(wf) == 90001


def test_bound_process_code_falls_back_to_dag_meta():
    c = _client()
    wf = SimpleNamespace(scheduler_definition_id=None, dag_config={"ds_process_code": "42"})
    assert c._bound_process_code(wf) == 42


def test_existing_code_uses_bound_id_not_name_lookup():
    c = _client()
    c._find_process_code = MagicMock(return_value=999)
    wf = SimpleNamespace(
        id=85,
        name="体育核心指标lark每天推送-大数据内",
        scheduler_definition_id="777",
        dag_config={},
    )
    assert c._existing_process_code_for_workflow(1, wf) == 777
    c._find_process_code.assert_not_called()


def test_existing_code_name_fallback_only_when_unbound():
    c = _client()
    c._find_process_code = MagicMock(return_value=555)
    wf = SimpleNamespace(
        id=85,
        name="体育核心指标lark每天推送",
        scheduler_definition_id=None,
        dag_config={},
    )
    assert c._existing_process_code_for_workflow(1, wf) == 555
    c._find_process_code.assert_called_once_with(1, "dw_85_体育核心指标lark每天推送")


# ---------- 集成（mock DS HTTP）：改名走 PUT 原 code ----------


def test_upsert_rename_updates_bound_code_not_create():
    """回归：GIDO 改名后再发布，必须 PUT 原 process code，禁止 POST 新建。"""
    c = _client()
    c._post = MagicMock(return_value={"code": 0, "data": {}})
    c._put = MagicMock(return_value={"code": 0, "data": {}})
    c._find_process_code = MagicMock(return_value=None)

    wf = SimpleNamespace(
        id=85,
        name="体育核心指标lark每天推送-大数据内",
        scheduler_definition_id="90085",
        dag_config={},
    )
    payload = {"name": f"dw_{wf.id}_{wf.name}", "description": ""}

    code = c._upsert_process_definition(1001, wf, payload)

    assert code == 90085
    c._put.assert_called_once()
    put_path, put_kwargs = c._put.call_args[0][0], c._put.call_args[1]
    assert put_path == "/projects/1001/process-definition/90085"
    assert put_kwargs["data"]["name"] == "dw_85_体育核心指标lark每天推送-大数据内"
    # 创建接口不得被调用（release 的 _post 可以有）
    create_posts = [
        call
        for call in c._post.call_args_list
        if call.args and call.args[0] == "/projects/1001/process-definition"
    ]
    assert create_posts == []
    c._find_process_code.assert_not_called()


def test_upsert_creates_when_unbound_and_name_missing():
    c = _client()
    c._post = MagicMock(return_value={"code": 0, "data": {"code": 12345}})
    c._put = MagicMock()
    c._find_process_code = MagicMock(return_value=None)
    wf = SimpleNamespace(id=1, name="new_wf", scheduler_definition_id=None, dag_config={})

    code = c._upsert_process_definition(9, wf, {"name": "dw_1_new_wf"})

    assert code == 12345
    c._put.assert_not_called()
    c._post.assert_called_with("/projects/9/process-definition", data={"name": "dw_1_new_wf"})


def test_upsert_updates_when_unbound_but_name_matches_legacy():
    c = _client()
    c._post = MagicMock(return_value={"code": 0, "data": {}})
    c._put = MagicMock(return_value={"code": 0, "data": {}})
    c._find_process_code = MagicMock(return_value=66)
    wf = SimpleNamespace(id=2, name="legacy", scheduler_definition_id=None, dag_config={})

    code = c._upsert_process_definition(9, wf, {"name": "dw_2_legacy"})

    assert code == 66
    c._put.assert_called_once_with("/projects/9/process-definition/66", data={"name": "dw_2_legacy"})
