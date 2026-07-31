# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Real Redis integration for multi-replica shared state.

Skip unless a Redis is reachable (default ``redis://127.0.0.1:6379/15``,
override with ``GIDO_TEST_REDIS_URL`` / ``REDIS_URL``).
"""
from __future__ import annotations

import threading
import uuid

import pytest

from app.services import shared_state
from app.services.copilot.session_store import SessionStore
from tests.ha_helpers import redis_reachable, redis_test_url

pytestmark = pytest.mark.integration


@pytest.fixture
def live_redis(monkeypatch):
    url = redis_test_url()
    if not redis_reachable(url):
        pytest.skip(f"Redis not reachable at {url} (set GIDO_TEST_REDIS_URL to enable)")
    monkeypatch.setattr(shared_state.settings, "SHARED_STATE_REQUIRED", True)
    monkeypatch.setattr(shared_state.settings, "REDIS_URL", url)
    monkeypatch.setattr(shared_state.settings, "REDIS_PASSWORD", "")
    monkeypatch.setattr(shared_state.settings, "SHARED_STATE_PREFIX", f"gido-test-{uuid.uuid4().hex[:8]}")
    shared_state.reset_redis_client_for_tests()
    client = shared_state.redis_client(required=True)
    assert client is not None
    yield client
    shared_state.reset_redis_client_for_tests()


def test_live_redis_rate_limit_and_claim(live_redis):
    bucket = f"rl-{uuid.uuid4().hex}"
    claim = f"claim-{uuid.uuid4().hex}"
    assert shared_state.rate_limit_hit(bucket, 2, 60) is False
    assert shared_state.rate_limit_hit(bucket, 2, 60) is False
    assert shared_state.rate_limit_hit(bucket, 2, 60) is True
    assert shared_state.claim_once(claim, 60) is True
    assert shared_state.claim_once(claim, 60) is False


def test_live_redis_claim_once_is_atomic_across_threads(live_redis):
    claim = f"race-{uuid.uuid4().hex}"
    results: list[bool | None] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait(timeout=5)
        results.append(shared_state.claim_once(claim, 60))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert results.count(True) == 1
    assert results.count(False) == 7


def test_live_redis_session_visible_to_second_store(live_redis, monkeypatch):
    monkeypatch.setattr("app.services.copilot.session_store.settings.SHARED_STATE_REQUIRED", True)
    monkeypatch.setattr(
        "app.services.copilot.session_store.settings.SHARED_STATE_PREFIX",
        shared_state.settings.SHARED_STATE_PREFIX,
    )
    writer = SessionStore()
    reader = SessionStore()
    sid = writer.create(42, 7)  # default title 「新对话」→ first user msg becomes title
    writer.append_messages(sid, 42, [{"role": "user", "content": "from-pod-a"}])
    session = reader.get(sid, 42)
    assert session is not None
    assert session["title"] == "from-pod-a"
    assert session["messages"][0]["content"] == "from-pod-a"
    assert reader.list_for_user(42, 7)[0]["id"] == sid
    assert reader.delete(sid, 42) is True
    assert writer.get(sid, 42) is None


def test_redis_ready_when_required(live_redis):
    assert shared_state.redis_ready() is True
