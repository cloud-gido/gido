# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import studio
from app.core.database import Base
from app.models.workspace import AdhocRun, AdhocRunLogChunk, TaskNode


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_internal_callback_submits_polls_and_cancels(monkeypatch):
    db = _db()
    node = TaskNode(
        workspace_id=7,
        name="scheduled-python",
        node_type="PYTHON",
        script_content="print('scheduled')",
        created_by=9,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    monkeypatch.setattr(studio.settings, "INTERNAL_TOKEN", "shared-token")
    monkeypatch.setattr(studio.settings, "ADHOC_ASYNC_ENABLED", True)

    submitted = studio.submit_internal_node_run(
        node.id,
        studio.RunNodeBody(bizdate="2026-09-17"),
        authorization="Bearer shared-token",
        db=db,
    )
    run_id = int(submitted.body.decode())
    row = db.query(AdhocRun).filter(AdhocRun.id == run_id).one()
    assert row.source == "scheduler"
    assert row.status == "queued"
    assert row.business_date == "2026-09-17"

    row.status = "running"
    db.add(
        AdhocRunLogChunk(
            run_id=run_id,
            seq=1,
            stream="stdout",
            content="scheduled log\n",
        )
    )
    db.commit()
    polled = studio.poll_internal_node_run(
        run_id,
        after_seq=0,
        authorization="Bearer shared-token",
        db=db,
    )
    assert polled.body.decode() == "running\n1\nscheduled log\n"

    cancelled = studio.cancel_internal_node_run(
        run_id,
        authorization="Bearer shared-token",
        db=db,
    )
    assert cancelled.body.decode() == "cancel_requested"


def test_internal_callback_reuses_active_submission(monkeypatch):
    db = _db()
    node = TaskNode(
        workspace_id=7,
        name="scheduled-python",
        node_type="PYTHON",
        script_content="print('scheduled')",
        created_by=9,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    monkeypatch.setattr(studio.settings, "INTERNAL_TOKEN", "shared-token")
    monkeypatch.setattr(studio.settings, "ADHOC_ASYNC_ENABLED", True)
    body = studio.RunNodeBody(bizdate="2026-09-17")

    first = studio.submit_internal_node_run(
        node.id,
        body,
        authorization="Bearer shared-token",
        db=db,
    )
    second = studio.submit_internal_node_run(
        node.id,
        body,
        authorization="Bearer shared-token",
        db=db,
    )

    assert second.body == first.body
    assert db.query(AdhocRun).count() == 1
