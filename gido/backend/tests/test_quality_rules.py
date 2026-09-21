# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据质量：阈值解析、规则类型校验、强弱标记。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-quality")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.workspace import AlertEvent, DataSource, MetaTable, QualityRule, User, Workspace
from app.models import rbac_models  # noqa: F401
from app.api.quality import (
    migrate_quality_rule_severity,
)
from app.services.quality_check import (
    SEVERITY_BLOCK,
    eval_threshold,
    normalize_severity,
    validate_rule_type,
)
from app.services.alert_center import open_quality_alert, resolve_quality_alerts


def test_eval_threshold_ops():
    assert eval_threshold(95, ">=95") == "pass"
    assert eval_threshold(94, ">=95") == "fail"
    assert eval_threshold(100, "==100") == "pass"
    assert eval_threshold(99, "=100") == "fail"
    assert eval_threshold(10, "<20") == "pass"
    assert eval_threshold(10, "<=10") == "pass"
    assert eval_threshold(11, ">10") == "pass"
    assert eval_threshold(90, "not-a-threshold") == "warning"
    assert eval_threshold(0, None) == "pass"


def test_validate_rule_type_allows_consistency():
    assert validate_rule_type("consistency") == "consistency"
    assert validate_rule_type("validity") == "validity"
    with pytest.raises(HTTPException) as ei:
        validate_rule_type("made_up")
    assert ei.value.status_code == 400


def test_normalize_severity():
    assert normalize_severity("block") == SEVERITY_BLOCK
    assert normalize_severity("strong") == SEVERITY_BLOCK
    assert normalize_severity("warn") == "warn"
    assert normalize_severity(None) == "warn"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    migrate_quality_rule_severity(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(User(id=1, username="u", email="u@t.local", hashed_password="x", is_active=True))
    s.add(Workspace(id=1, name="ws", owner_id=1))
    s.add(
        DataSource(
            id=10,
            workspace_id=1,
            name="doris",
            ds_type="doris",
            host="h",
            port=9030,
            database="bigdata_dw",
            created_by=1,
        )
    )
    s.add(
        MetaTable(
            id=100,
            workspace_id=1,
            datasource_id=10,
            db_name="bigdata_dw",
            table_name="dwd_orders",
            table_type="table",
        )
    )
    s.commit()
    yield s
    s.close()


def test_quality_alert_open_and_resolve(db):
    rule = QualityRule(
        workspace_id=1,
        table_id=100,
        rule_name="orders_complete",
        rule_type="completeness",
        threshold=">=95",
        severity="block",
        is_active=True,
        created_by=1,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    event = open_quality_alert(
        db,
        workspace_id=1,
        rule=rule,
        score=80,
        threshold=">=95",
        blocking=True,
        notify=False,
    )
    db.commit()
    assert event is not None
    assert event.alert_type == "quality"
    assert event.status == "open"
    assert "阻断" in (event.message or "")

    again = open_quality_alert(
        db,
        workspace_id=1,
        rule=rule,
        score=70,
        threshold=">=95",
        blocking=True,
        notify=False,
    )
    db.commit()
    assert again.id == event.id
    assert db.query(AlertEvent).filter(AlertEvent.alert_type == "quality").count() == 1

    n = resolve_quality_alerts(db, rule_id=rule.id)
    db.commit()
    assert n == 1
    db.refresh(event)
    assert event.status == "resolved"


def test_partition_and_bizdate_helpers():
    from app.services.quality_check import expand_bizdate, partition_clause, resolve_bizdate

    assert expand_bizdate("dt={bizdate}", "2026-09-19") == "dt=2026-09-19"
    assert "2026-09-18" in expand_bizdate("{bizdate-1}", "2026-09-19")
    sql, params, label = partition_clause(
        {"partition_column": "dt", "partition_value": "{bizdate}"},
        "2026-09-19",
    )
    assert sql == "`dt` = %s"
    assert params == ("2026-09-19",)
    assert label == "dt=2026-09-19"
    assert len(resolve_bizdate(None)) == 10


def test_migrate_severity_idempotent():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    migrate_quality_rule_severity(engine)
    migrate_quality_rule_severity(engine)
    from sqlalchemy import inspect as sa_inspect

    cols = {c["name"] for c in sa_inspect(engine).get_columns("dw_quality_rules")}
    assert "severity" in cols
    assert "schedule_cron" in cols
