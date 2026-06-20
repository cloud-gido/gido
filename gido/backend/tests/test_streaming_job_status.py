# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./pytest_gido_meta.db")

from app.api.streaming import (
    StreamingJob,
    _operator_cr_spec_job_suspended,
    _preserve_platform_cancelled_on_flink_sync,
)


def test_operator_cr_spec_suspended():
    assert _operator_cr_spec_job_suspended({"spec": {"job": {"state": "suspended"}}}) is True
    assert _operator_cr_spec_job_suspended({"spec": {"job": {"state": "running"}}}) is False
    assert _operator_cr_spec_job_suspended({"spec": {"job": {}}}) is None


def test_preserve_cancelled_when_operator_still_suspended():
    job = StreamingJob(status="cancelled", updated_at=datetime.utcnow())
    assert _preserve_platform_cancelled_on_flink_sync(
        job, "RUNNING", operator_spec_suspended=True
    )


def test_reconcile_cancelled_to_running_when_operator_active():
    job = StreamingJob(status="cancelled", updated_at=datetime.utcnow() - timedelta(minutes=5))
    assert not _preserve_platform_cancelled_on_flink_sync(
        job, "RUNNING", operator_spec_suspended=False
    )


def test_session_cancel_grace_period():
    job = StreamingJob(status="cancelled", updated_at=datetime.utcnow() - timedelta(seconds=10))
    assert _preserve_platform_cancelled_on_flink_sync(job, "RUNNING", operator_spec_suspended=None)
    job.updated_at = datetime.utcnow() - timedelta(minutes=5)
    assert not _preserve_platform_cancelled_on_flink_sync(job, "RUNNING", operator_spec_suspended=None)
