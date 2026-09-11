# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""「今日实例」按工作空间时区算，而不是 UTC 日界线——否则上海的人早上八点前看到的「今天」还是昨天。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-local-day")

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.operation import _local_day_start_utc
from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.models.workspace import Workspace


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


def _ws(db, tz: str) -> int:
    ws = Workspace(name=f"ws-{tz}", timezone=tz)
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return int(ws.id)


def test_shanghai_day_starts_at_utc_1600_previous_day(db, monkeypatch):
    """上海 2026-09-11 00:00 == UTC 2026-09-10 16:00。"""
    ws_id = _ws(db, "Asia/Shanghai")
    # UTC 03:00 → 上海 11:00，当天是 09-11
    monkeypatch.setattr("app.api.operation.datetime", _FrozenUtc(datetime(2026, 9, 11, 3, 0)))
    assert _local_day_start_utc(db, ws_id) == datetime(2026, 9, 10, 16, 0)


def test_early_utc_morning_still_counts_as_the_same_shanghai_day(db, monkeypatch):
    """
    UTC 2026-09-11 01:00 是上海 09:00，同一个上海自然日。
    按 UTC 日界线算会把 09-11 00:00 之后的实例都算进来，漏掉上海清晨（UTC 前一天 16:00 起）那批。
    """
    ws_id = _ws(db, "Asia/Shanghai")
    monkeypatch.setattr("app.api.operation.datetime", _FrozenUtc(datetime(2026, 9, 11, 1, 0)))
    start = _local_day_start_utc(db, ws_id)
    assert start == datetime(2026, 9, 10, 16, 0)
    assert start < datetime(2026, 9, 11, 0, 0), "上海的今天比 UTC 的今天开始得更早"


def test_utc_workspace_matches_utc_midnight(db, monkeypatch):
    ws_id = _ws(db, "UTC")
    monkeypatch.setattr("app.api.operation.datetime", _FrozenUtc(datetime(2026, 9, 11, 3, 0)))
    assert _local_day_start_utc(db, ws_id) == datetime(2026, 9, 11, 0, 0)


def test_western_timezone_day_starts_later_than_utc(db, monkeypatch):
    """纽约（UTC-4，夏令时）的 09-11 00:00 是 UTC 09-11 04:00。"""
    ws_id = _ws(db, "America/New_York")
    monkeypatch.setattr("app.api.operation.datetime", _FrozenUtc(datetime(2026, 9, 11, 12, 0)))
    assert _local_day_start_utc(db, ws_id) == datetime(2026, 9, 11, 4, 0)


def test_missing_or_bad_timezone_falls_back_to_shanghai(db, monkeypatch):
    """时区字段为空或写错时不能抛异常，概览页不该因为一个配置项打不开。"""
    ws_id = _ws(db, "Not/AZone")
    monkeypatch.setattr("app.api.operation.datetime", _FrozenUtc(datetime(2026, 9, 11, 3, 0)))
    assert _local_day_start_utc(db, ws_id) == datetime(2026, 9, 10, 16, 0)
    # 工作空间根本不存在也一样兜底
    assert _local_day_start_utc(db, 999999) == datetime(2026, 9, 10, 16, 0)


class _FrozenUtc:
    """只冻结 utcnow，其余属性透给真 datetime。"""

    def __init__(self, now: datetime):
        self._now = now

    def utcnow(self):
        return self._now

    def __getattr__(self, item):
        return getattr(datetime, item)
