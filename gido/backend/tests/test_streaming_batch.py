# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./pytest_gido_meta.db")

from app.services.streaming_batch import validate_batch_job_ids, BATCH_ACTION_START, BATCH_ACTION_CANCEL


class _Job:
    def __init__(self, id: int, name: str, status: str):
        self.id = id
        self.name = name
        self.status = status


def test_batch_start_rejects_running():
    jobs = [_Job(1, "a", "draft"), _Job(2, "b", "running")]
    try:
        validate_batch_job_ids(jobs, BATCH_ACTION_START, max_jobs=100)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "running" in str(e).lower()


def test_batch_start_allows_non_running():
    jobs = [_Job(1, "a", "draft"), _Job(2, "b", "cancelled")]
    out, _ = validate_batch_job_ids(jobs, BATCH_ACTION_START, max_jobs=100)
    assert len(out) == 2


def test_batch_max_limit():
    jobs = [_Job(i, f"j{i}", "draft") for i in range(101)]
    try:
        validate_batch_job_ids(jobs, BATCH_ACTION_CANCEL, max_jobs=100)
        assert False
    except ValueError as e:
        assert "100" in str(e)
