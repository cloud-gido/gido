# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./pytest_gido_meta.db")

from app.api import streaming as streaming_api


def test_summarize_checkpoints_latest():
    raw = {
        "counts": {"completed": 3, "failed": 1, "in_progress": 0, "total": 4, "restored": 0},
        "latest": {
            "completed": {
                "id": 10,
                "status": "COMPLETED",
                "external_path": "s3://b/c",
                "end_to_end_duration": 1200,
                "state_size": 99,
            },
            "failed": {"id": 9, "failure_message": "boom"},
            "in_progress": None,
        },
    }
    s = streaming_api._summarize_checkpoints(raw)
    assert s["counts"]["completed"] == 3
    assert s["latest_completed"]["id"] == 10
    assert s["latest_completed"]["path"] == "s3://b/c"
    assert s["latest_failed"]["failure_message"] == "boom"


def test_build_failure_payload_from_submit_error():
    from types import SimpleNamespace

    job = SimpleNamespace(status="failed", last_submit_error="ClassNotFound")
    f = streaming_api._build_failure_payload(job, flink_status="FAILED", note=None)
    assert f is not None
    assert f["source"] == "submit"
    assert "ClassNotFound" in f["message"]
