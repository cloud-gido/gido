# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""失败实例立刻写告警，飞书渠道打开即推送（即使总开关未开）。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-alert")

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.models.workspace import (
    AlertEvent,
    AlertNotificationConfig,
    JobVersion,
    User,
    Workflow,
    WorkflowInstance,
    Workspace,
)
from app.services.alert_notification import dispatch_pending_notifications, notify_alert_event
from app.services.dolphin_instance_sync import ingest_ds_instance_from_callback, sync_from_dolphin_definitions


def _flush_outbox(db):
    """写入只进 pending；测试里显式跑一轮出站箱，模拟后台投递任务。"""
    dispatch_pending_notifications(db)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    user = User(
        username="alert-admin",
        email="alert@gido.test",
        hashed_password="x",
        is_admin=True,
        is_active=True,
    )
    session.add(user)
    session.flush()
    ws = Workspace(name="alert-ws", owner_id=user.id, timezone="Asia/Shanghai")
    session.add(ws)
    session.commit()
    yield session
    session.close()


def test_notify_lark_when_channel_on_even_if_master_disabled(db):
    cfg = AlertNotificationConfig(
        workspace_id=1,
        enabled=False,
        min_severity="error",
        lark_enabled=True,
        lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
    )
    db.add(cfg)
    event = AlertEvent(
        workspace_id=1,
        alert_type="failed",
        level="error",
        severity="error",
        status="open",
        message="boom",
    )
    db.add(event)
    db.flush()

    with patch("app.services.alert_notification._post_json") as post:
        out = notify_alert_event(db, event)
    assert out["sent"] == ["lark"]
    assert event.notification_status == "sent"
    post.assert_called_once()
    url, payload = post.call_args[0]
    assert "feishu.cn" in url
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["template"] == "red"
    assert "调度失败" in payload["card"]["header"]["title"]["content"]
    blob = str(payload)
    assert "告警级别：" not in blob


def test_ingest_failed_callback_opens_alert_and_posts_lark(db):
    ws = db.query(Workspace).first()
    wf = Workflow(
        workspace_id=ws.id,
        name="体育线-风控",
        dag_config={"nodes": []},
        scheduler_project_id="1001",
        scheduler_definition_id="90001",
        status="published",
    )
    db.add(wf)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=False,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/prod",
            notify_armed_at=datetime(2020, 1, 1),
        )
    )
    db.commit()

    with patch("app.services.alert_notification._post_json") as post:
        inst = ingest_ds_instance_from_callback(
            db,
            scheduler_instance_id="252045",
            dw_status="failed",
            raw_state="FAILURE",
            project_id="1001",
            definition_id="90001",
        )
        _flush_outbox(db)
        db.commit()

    assert inst is not None
    assert inst.status == "failed"
    ev = db.query(AlertEvent).filter(AlertEvent.workflow_instance_id == inst.id).one()
    assert ev.status == "open"
    assert ev.notification_status == "sent"
    assert post.call_count == 1


def test_sync_ingests_already_failed_instance_and_alerts(db):
    ws = db.query(Workspace).first()
    wf = Workflow(
        workspace_id=ws.id,
        name="dwd_gameline_risk_assessment",
        dag_config={"nodes": []},
        scheduler_project_id="11",
        scheduler_definition_id="22",
        status="published",
    )
    db.add(wf)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/sync",
        )
    )
    db.commit()

    ds = SimpleNamespace()
    ds.list_process_instances = MagicMock(
        return_value=[
            {
                "id": 777,
                "state": "FAILURE",
                "commandType": "SCHEDULER",
                "startTime": "2026-01-01 00:00:00",
                "endTime": "2026-01-01 00:00:01",
            }
        ]
    )
    ds.list_task_instances_all = MagicMock(return_value=[])

    with patch("app.services.alert_notification._post_json") as post:
        stats = sync_from_dolphin_definitions(db, ds)
        _flush_outbox(db)
        db.commit()

    assert stats["ingested"] == 1
    inst = db.query(WorkflowInstance).one()
    assert inst.status == "failed"
    ev = db.query(AlertEvent).filter(AlertEvent.workflow_instance_id == inst.id).one()
    assert ev.status == "open"
    assert ev.notification_status == "skipped"
    assert post.call_count == 0


def test_recent_failed_instance_still_posts_lark(db):
    from zoneinfo import ZoneInfo

    ws = db.query(Workspace).first()
    wf = Workflow(
        workspace_id=ws.id,
        name="fresh-fail",
        dag_config={"nodes": []},
        scheduler_project_id="11",
        scheduler_definition_id="22",
        status="published",
    )
    db.add(wf)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/fresh",
            notify_armed_at=datetime.utcnow() - timedelta(hours=1),
        )
    )
    db.commit()
    now_sh = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    ds = SimpleNamespace()
    ds.list_process_instances = MagicMock(
        return_value=[{
            "id": 778,
            "state": "FAILURE",
            "commandType": "SCHEDULER",
            "startTime": now_sh,
            "endTime": now_sh,
        }]
    )
    ds.list_task_instances_all = MagicMock(return_value=[])
    with patch("app.services.alert_notification._post_json") as post:
        sync_from_dolphin_definitions(db, ds)
        _flush_outbox(db)
        db.commit()
    assert post.call_count == 1
    payload = post.call_args[0][1]
    assert payload["msg_type"] == "interactive"
    assert any(el.get("tag") == "hr" for el in payload["card"]["elements"])


def test_second_failure_same_workflow_skips_lark_during_cooldown(db):
    ws = db.query(Workspace).first()
    wf = Workflow(
        workspace_id=ws.id,
        name="cooldown-wf",
        dag_config={"nodes": []},
        scheduler_project_id="11",
        scheduler_definition_id="22",
        status="published",
    )
    db.add(wf)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            notify_armed_at=datetime.utcnow() - timedelta(hours=1),
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/cd",
            notify_cooldown_minutes=15,
        )
    )
    db.commit()
    inst1 = WorkflowInstance(workflow_id=wf.id, status="failed", trigger_type="schedule")
    inst2 = WorkflowInstance(workflow_id=wf.id, status="failed", trigger_type="schedule")
    db.add_all([inst1, inst2])
    db.flush()

    from app.services.alert_center import open_instance_alert
    from app.services.alert_notification import dispatch_pending_notifications

    with patch("app.services.alert_notification._post_json") as post:
        e1 = open_instance_alert(db, workflow_instance=inst1, notify=True)
        e2 = open_instance_alert(db, workflow_instance=inst2, notify=True)
        db.commit()
        dispatch_pending_notifications(db)
        db.refresh(e1)
        db.refresh(e2)

    assert post.call_count == 1
    assert e1.notification_status == "sent"
    assert e2.notification_status == "skipped"


def test_recovery_notifies_even_below_min_severity(db):
    ws = db.query(Workspace).first()
    wf = Workflow(
        workspace_id=ws.id,
        name="recover-wf",
        dag_config={"nodes": []},
        status="published",
    )
    db.add(wf)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/rec",
        )
    )
    db.commit()
    inst = WorkflowInstance(workflow_id=wf.id, status="success", trigger_type="schedule")
    db.add(inst)
    db.flush()
    failed = AlertEvent(
        workspace_id=ws.id,
        workflow_id=wf.id,
        workflow_instance_id=inst.id,
        alert_type="failed",
        level="error",
        severity="error",
        status="open",
        message="boom",
        notification_status="sent",
    )
    db.add(failed)
    db.flush()

    from app.services.alert_center import resolve_instance_alerts_on_recovery
    from app.services.alert_notification import dispatch_pending_notifications

    with patch("app.services.alert_notification._post_json") as post:
        rec = resolve_instance_alerts_on_recovery(db, inst)
        db.commit()
        dispatch_pending_notifications(db)
        if rec is not None:
            db.refresh(rec)

    assert rec is not None
    assert rec.alert_type == "recovered"
    assert rec.notification_status == "sent"
    assert failed.status == "resolved"
    assert post.call_count == 1
    payload = post.call_args[0][1]
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["template"] == "green"


def test_node_failure_alert_does_not_post_lark(db):
    from app.models.workspace import NodeInstance, TaskNode
    from app.services.alert_center import open_instance_alert

    ws = db.query(Workspace).first()
    wf = Workflow(workspace_id=ws.id, name="node-wf", dag_config={"nodes": []}, status="published")
    db.add(wf)
    db.flush()
    node = TaskNode(workspace_id=ws.id, name="dwd_x", node_type="SQL")
    db.add(node)
    db.flush()
    inst = WorkflowInstance(workflow_id=wf.id, status="failed", trigger_type="schedule")
    db.add(inst)
    db.flush()
    ni = NodeInstance(workflow_instance_id=inst.id, node_id=node.id, status="failed")
    db.add(ni)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/node",
        )
    )
    db.flush()

    with patch("app.services.alert_notification._post_json") as post:
        ev = open_instance_alert(db, workflow_instance=inst, node_instance=ni, notify=False)

    assert ev.notification_status == "skipped"
    post.assert_not_called()


def test_lark_card_includes_instance_center_button(db):
    ws = db.query(Workspace).first()
    wf = Workflow(workspace_id=ws.id, name="card-wf", dag_config={"nodes": []}, status="published")
    db.add(wf)
    db.flush()
    inst = WorkflowInstance(workflow_id=wf.id, status="failed", trigger_type="schedule")
    db.add(inst)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/btn",
        )
    )
    db.flush()
    event = AlertEvent(
        workspace_id=ws.id,
        workflow_id=wf.id,
        workflow_instance_id=inst.id,
        alert_type="failed",
        level="error",
        severity="error",
        status="open",
        message="boom",
    )
    db.add(event)
    db.flush()

    with patch("app.services.alert_notification.gido_public_url", return_value="https://gido.example.com"):
        with patch("app.services.alert_notification._post_json") as post:
            notify_alert_event(db, event)

    payload = post.call_args[0][1]
    actions = [el for el in payload["card"]["elements"] if el.get("tag") == "action"]
    assert actions
    assert actions[0]["actions"][0]["text"]["content"] == "查看详情"
    assert "operation?workspace_id=" in actions[0]["actions"][0]["url"]
    assert f"instance={inst.id}" in actions[0]["actions"][0]["url"]


def test_test_channel_card_is_not_plain_dump(db):
    ws = db.query(Workspace).first()
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/testcard",
        )
    )
    event = AlertEvent(
        workspace_id=ws.id,
        alert_type="test",
        level="info",
        severity="info",
        status="open",
        message="这是一条 GIDO 告警通知测试消息。",
    )
    db.add(event)
    db.flush()
    with patch("app.services.alert_notification.gido_public_url", return_value="https://gido.example.com"):
        with patch("app.services.alert_notification._post_json") as post:
            notify_alert_event(db, event, force=True)
    payload = post.call_args[0][1]
    assert payload["msg_type"] == "interactive"
    title = payload["card"]["header"]["title"]["content"]
    assert "通道测试" in title
    blob = str(payload)
    assert "告警级别：info" not in blob
    assert "工作流实例：#-" not in blob
    actions = [el for el in payload["card"]["elements"] if el.get("tag") == "action"]
    assert actions
    assert actions[0]["actions"][0]["text"]["content"] == "打开告警中心"
    assert "/gido/batch/alert?workspace_id=" in actions[0]["actions"][0]["url"]


def test_workspace_alert_coverage_hints_wrong_space_and_missing_site(db):
    from app.services.alert_center import workspace_alert_coverage

    ws = db.query(Workspace).first()
    empty = workspace_alert_coverage(db, ws.id)
    assert empty["published_workflow_count"] == 0
    assert any("没有已发布" in h for h in empty["hints"])

    db.add(
        Workflow(
            workspace_id=ws.id,
            name="体育线-风控",
            dag_config={"nodes": []},
            scheduler_project_id="11",
            scheduler_definition_id="90001",
            status="published",
        )
    )
    db.commit()
    with patch("app.services.alert_notification.gido_public_url", return_value=""):
        cov = workspace_alert_coverage(db, ws.id)
    assert cov["published_workflow_count"] == 1
    assert cov["lark_enabled"] is False
    assert cov["site_url_configured"] is False
    assert any("飞书渠道未打开" in h for h in cov["hints"])
    assert any("站点入口" in h for h in cov["hints"])
    assert "体育线-风控" in cov["published_workflow_names"]


def test_list_alerts_hides_pre_arm_skipped_and_filters_workflow_name(db):
    from app.api import alert as alert_mod
    from app.api.alert import list_alerts

    alert_mod._meta_local.clear()

    ws = db.query(Workspace).first()
    user = db.query(User).first()
    armed = datetime.utcnow()
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/x",
            notify_armed_at=armed,
        )
    )
    wf = Workflow(
        workspace_id=ws.id,
        name="体育线-风控",
        dag_config={"nodes": []},
        scheduler_definition_id="22",
        status="published",
    )
    other = Workflow(
        workspace_id=ws.id,
        name="其它作业",
        dag_config={"nodes": []},
        scheduler_definition_id="23",
        status="published",
    )
    db.add_all([wf, other])
    db.flush()
    old_inst = WorkflowInstance(
        workflow_id=wf.id,
        status="failed",
        finished_at=datetime.utcnow() - timedelta(days=3),
    )
    new_inst = WorkflowInstance(
        workflow_id=wf.id,
        status="failed",
        finished_at=datetime.utcnow(),
    )
    other_inst = WorkflowInstance(
        workflow_id=other.id,
        status="failed",
        finished_at=datetime.utcnow(),
    )
    db.add_all([old_inst, new_inst, other_inst])
    db.flush()
    db.add_all(
        [
            AlertEvent(
                workspace_id=ws.id,
                workflow_id=wf.id,
                workflow_instance_id=old_inst.id,
                alert_type="failed",
                level="error",
                severity="error",
                status="open",
                message="old",
                notification_status="skipped",
            ),
            AlertEvent(
                workspace_id=ws.id,
                workflow_id=wf.id,
                workflow_instance_id=new_inst.id,
                alert_type="failed",
                level="error",
                severity="error",
                status="open",
                message="new",
                notification_status="sent",
            ),
            AlertEvent(
                workspace_id=ws.id,
                workflow_id=other.id,
                workflow_instance_id=other_inst.id,
                alert_type="failed",
                level="error",
                severity="error",
                status="open",
                message="other",
                notification_status="sent",
            ),
        ]
    )
    db.commit()

    default = list_alerts(
        workspace_id=ws.id,
        status="open",
        page=1,
        page_size=50,
        include_all_workspaces=False,
        q=None,
        workflow_id=None,
        notification_status=None,
        after_armed=True,
        db=db,
        current_user=user,
    )
    assert default["total"] == 2
    assert {row["message"] for row in default["items"]} == {"new", "other"}
    assert default["coverage"]["published_workflow_count"] == 2

    historic = list_alerts(
        workspace_id=ws.id,
        status="open",
        page=1,
        page_size=50,
        include_all_workspaces=False,
        q=None,
        workflow_id=None,
        notification_status=None,
        after_armed=False,
        db=db,
        current_user=user,
    )
    assert historic["total"] == 3

    named = list_alerts(
        workspace_id=ws.id,
        status="open",
        page=1,
        page_size=50,
        include_all_workspaces=False,
        q="风控",
        workflow_id=None,
        notification_status=None,
        after_armed=True,
        db=db,
        current_user=user,
    )
    assert named["total"] == 1
    assert named["items"][0]["workflow_name"] == "体育线-风控"

    by_wf = list_alerts(
        workspace_id=ws.id,
        status="open",
        page=1,
        page_size=50,
        include_all_workspaces=False,
        q=None,
        workflow_id=int(wf.id),
        notification_status=None,
        after_armed=True,
        db=db,
        current_user=user,
    )
    assert by_wf["total"] == 1
    assert by_wf["items"][0]["workflow_id"] == wf.id
    assert "workflow_created_by_username" in by_wf["items"][0]

    skipped = list_alerts(
        workspace_id=ws.id,
        status="open",
        page=1,
        page_size=50,
        include_all_workspaces=False,
        q=None,
        workflow_id=None,
        notification_status="skipped",
        after_armed=False,
        db=db,
        current_user=user,
    )
    assert skipped["total"] == 1
    assert skipped["items"][0]["notification_status"] == "skipped"


def test_workspace_alert_coverage_hints_unbound_definition(db):
    from app.services.alert_center import workspace_alert_coverage

    ws = db.query(Workspace).first()
    db.add(
        Workflow(
            workspace_id=ws.id,
            name="未绑定流程",
            dag_config={"nodes": []},
            status="published",
        )
    )
    db.commit()
    with patch("app.services.alert_notification.gido_public_url", return_value="https://gido.example"):
        cov = workspace_alert_coverage(db, ws.id)
    assert any("没有生效的生产调度绑定" in h for h in cov["hints"])


def test_list_process_instances_sends_date_window_and_failure_state():
    from app.services.dolphin import DSClient

    client = DSClient.__new__(DSClient)
    client._get = MagicMock(return_value={"code": 0, "data": {"totalList": [{"id": 9}]}})
    rows = client.list_process_instances(
        1001,
        process_definition_code=22,
        page_size=50,
        start_date="2026-09-01 00:00:00",
        end_date="2026-09-11 23:59:59",
        state_type="FAILURE",
    )
    assert rows[0]["id"] == 9
    params = client._get.call_args.kwargs["params"]
    assert params["processDefineCode"] == 22
    assert params["startDate"] == "2026-09-01 00:00:00"
    assert params["endDate"] == "2026-09-11 23:59:59"
    assert params["stateType"] == "FAILURE"


def test_list_process_instances_page_reads_total_page():
    from app.services.dolphin import DSClient

    client = DSClient.__new__(DSClient)
    client._get = MagicMock(
        return_value={
            "code": 0,
            "data": {"totalList": [{"id": 1}], "total": 4800, "totalPage": 48, "currentPage": 1},
        }
    )
    page = client.list_process_instances_page(1001, process_definition_code=22, page_no=1)
    assert page["total_page"] == 48
    assert page["rows"][0]["id"] == 1


def test_sync_ingests_failure_from_last_page_when_first_page_is_old(db):
    from zoneinfo import ZoneInfo

    ws = db.query(Workspace).first()
    wf = Workflow(
        workspace_id=ws.id,
        name="last-page-fail",
        dag_config={"nodes": []},
        scheduler_project_id="11",
        scheduler_definition_id="22",
        status="published",
    )
    db.add(wf)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/lastpage",
            notify_armed_at=datetime.utcnow() - timedelta(hours=1),
        )
    )
    db.commit()
    now_sh = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

    def list_page(_project, process_definition_code=None, page_no=1, page_size=100, state_type=None, **_kw):
        if state_type != "FAILURE":
            return {"rows": [], "total_page": 1}
        if page_no == 1:
            return {
                "rows": [{
                    "id": 1,
                    "state": "FAILURE",
                    "commandType": "SCHEDULER",
                    "startTime": "2026-08-06 16:00:00",
                    "endTime": "2026-08-06 16:08:00",
                }],
                "total_page": 48,
            }
        if page_no == 48:
            return {
                "rows": [{
                    "id": 252045,
                    "state": "FAILURE",
                    "commandType": "SCHEDULER",
                    "startTime": now_sh,
                    "endTime": now_sh,
                }],
                "total_page": 48,
            }
        return {"rows": [], "total_page": 48}

    ds = SimpleNamespace()
    ds.list_process_instances_page = list_page
    ds.list_task_instances_all = MagicMock(return_value=[])
    with patch("app.services.alert_notification._post_json") as post:
        stats = sync_from_dolphin_definitions(db, ds)
        _flush_outbox(db)
        db.commit()
    ids = {row.scheduler_instance_id for row in db.query(WorkflowInstance).all()}
    assert "252045" in ids
    assert stats["ingested"] >= 1
    assert db.query(AlertEvent).filter(AlertEvent.alert_type == "failed").count() >= 1
    assert post.call_count >= 1



def test_sync_ingests_failure_even_if_recent_page_is_all_success(db):
    from zoneinfo import ZoneInfo

    ws = db.query(Workspace).first()
    wf = Workflow(
        workspace_id=ws.id,
        name="buried-fail",
        dag_config={"nodes": []},
        scheduler_project_id="11",
        scheduler_definition_id="22",
        status="published",
    )
    db.add(wf)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/buried",
            notify_armed_at=datetime.utcnow() - timedelta(hours=1),
        )
    )
    db.commit()
    now_sh = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

    def list_pi(_project, process_definition_code=None, page_size=100, start_date=None, end_date=None, state_type=None, **_kw):
        if state_type == "FAILURE":
            return [{
                "id": 42,
                "state": "FAILURE",
                "commandType": "SCHEDULER",
                "startTime": now_sh,
                "endTime": now_sh,
            }]
        return [{
            "id": 41,
            "state": "SUCCESS",
            "commandType": "SCHEDULER",
            "startTime": now_sh,
            "endTime": now_sh,
        }]

    ds = SimpleNamespace()
    ds.list_process_instances = list_pi
    ds.list_task_instances_all = MagicMock(return_value=[])
    with patch("app.services.alert_notification._post_json") as post:
        stats = sync_from_dolphin_definitions(db, ds)
        _flush_outbox(db)
        db.commit()
    ids = {row.scheduler_instance_id for row in db.query(WorkflowInstance).all()}
    assert "42" in ids
    assert stats["ingested"] >= 1
    assert db.query(AlertEvent).filter(AlertEvent.alert_type == "failed").count() == 1
    assert post.call_count == 1


def test_sync_alerts_when_instance_bound_to_old_job_version(db):
    from zoneinfo import ZoneInfo

    ws = db.query(Workspace).first()
    wf = Workflow(
        workspace_id=ws.id,
        name="version-mismatch",
        dag_config={"nodes": []},
        scheduler_project_id="11",
        scheduler_definition_id="22",
        status="published",
    )
    db.add(wf)
    db.flush()
    old_ver = JobVersion(workflow_id=wf.id, version_no=1, status="archived", dag_snapshot={})
    new_ver = JobVersion(workflow_id=wf.id, version_no=2, status="active", dag_snapshot={})
    db.add_all([old_ver, new_ver])
    db.flush()
    wf.active_version_id = new_ver.id
    inst = WorkflowInstance(
        workflow_id=wf.id,
        job_version_id=old_ver.id,
        status="success",
        scheduler_instance_id="9001",
        trigger_type="schedule|ds:9001",
        started_at=datetime.utcnow() - timedelta(minutes=5),
        finished_at=datetime.utcnow() - timedelta(minutes=4),
    )
    db.add(inst)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/ver",
            notify_armed_at=datetime.utcnow() - timedelta(hours=1),
        )
    )
    db.commit()
    now_sh = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    ds = SimpleNamespace()
    ds.list_process_instances = MagicMock(
        return_value=[{
            "id": 9001,
            "state": "FAILURE",
            "commandType": "SCHEDULER",
            "startTime": now_sh,
            "endTime": now_sh,
        }]
    )
    ds.list_task_instances_all = MagicMock(return_value=[])
    with patch("app.services.alert_notification._post_json") as post:
        stats = sync_from_dolphin_definitions(db, ds)
        _flush_outbox(db)
        db.commit()
    assert stats["ingested"] == 0
    db.refresh(inst)
    assert inst.status == "failed"
    ev = db.query(AlertEvent).filter(AlertEvent.workflow_instance_id == inst.id).one()
    assert ev.status == "open"
    assert post.call_count == 1


def test_sync_project_level_failure_when_definition_list_is_empty(db):
    from zoneinfo import ZoneInfo

    ws = db.query(Workspace).first()
    wf = Workflow(
        workspace_id=ws.id,
        name="project-fail",
        dag_config={"nodes": []},
        scheduler_project_id="11",
        scheduler_definition_id="22",
        status="published",
    )
    db.add(wf)
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/proj",
            notify_armed_at=datetime.utcnow() - timedelta(hours=1),
        )
    )
    db.commit()
    now_sh = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

    def list_page(_project, process_definition_code=None, page_no=1, state_type=None, **_kw):
        if process_definition_code:
            return {"rows": [], "total_page": 1}
        if state_type == "FAILURE":
            return {
                "rows": [{
                    "id": 88801,
                    "processDefinitionCode": 22,
                    "state": "FAILURE",
                    "commandType": "SCHEDULER",
                    "startTime": now_sh,
                    "endTime": now_sh,
                }],
                "total_page": 1,
            }
        return {"rows": [], "total_page": 1}

    ds = SimpleNamespace()
    ds.list_process_instances_page = list_page
    ds.list_task_instances_all = MagicMock(return_value=[])
    with patch("app.services.alert_notification._post_json") as post:
        stats = sync_from_dolphin_definitions(db, ds)
        _flush_outbox(db)
        db.commit()
    ids = {row.scheduler_instance_id for row in db.query(WorkflowInstance).all()}
    assert "88801" in ids
    assert stats["ingested"] >= 1
    assert db.query(AlertEvent).filter(AlertEvent.alert_type == "failed").count() == 1
    assert post.call_count == 1

