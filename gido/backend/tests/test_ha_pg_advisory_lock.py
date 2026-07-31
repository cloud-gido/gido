# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""PostgreSQL advisory lock coverage for multi-replica mutual exclusion.

Default CI (SQLite) covers lock-id stability + in-process mutex.
Cross-connection tests run when ``GIDO_TEST_DATABASE_URL`` / ``DATABASE_URL``
points at a reachable PostgreSQL.
"""
from __future__ import annotations

import threading
import uuid

import pytest

from app.services import distributed_lock
from app.services.distributed_lock import _lock_id, acquire_distributed_lock, try_distributed_lock
from tests.ha_helpers import pg_reachable, pg_test_url

pytestmark = pytest.mark.integration

_PG_URL = pg_test_url()
_HAS_PG = pg_reachable(_PG_URL)


def test_lock_id_is_stable_signed_bigint():
    a = _lock_id("gido:cdc:job-1")
    b = _lock_id("gido:cdc:job-1")
    c = _lock_id("gido:cdc:job-2")
    assert a == b
    assert a != c
    assert -(2**63) <= a < 2**63


def test_local_mutex_blocks_nested_acquire():
    name = f"local-{uuid.uuid4().hex}"
    with try_distributed_lock(name) as first:
        assert first is True
        with try_distributed_lock(name) as second:
            assert second is False


def test_local_mutex_released_for_next_caller():
    name = f"local-seq-{uuid.uuid4().hex}"
    with try_distributed_lock(name) as first:
        assert first is True
    with try_distributed_lock(name) as second:
        assert second is True


@pytest.fixture
def pg_engine(monkeypatch):
    if not _HAS_PG:
        pytest.skip(
            "PostgreSQL not reachable "
            f"(set GIDO_TEST_DATABASE_URL to enable; got {_PG_URL!r})"
        )
    from sqlalchemy import create_engine

    engine = create_engine(_PG_URL, pool_pre_ping=True, pool_size=4)
    monkeypatch.setattr(distributed_lock, "engine", engine)
    yield engine
    engine.dispose()


def test_pg_advisory_lock_blocks_second_connection(pg_engine):
    name = f"pg-block-{uuid.uuid4().hex}"
    first = acquire_distributed_lock(name)
    assert first is not None
    try:
        second = acquire_distributed_lock(name)
        assert second is None
    finally:
        first.release()
    third = acquire_distributed_lock(name)
    assert third is not None
    third.release()


def test_pg_advisory_lock_only_one_winner_across_threads(pg_engine):
    import time

    name = f"pg-race-{uuid.uuid4().hex}"
    results: list[bool] = []
    lock = threading.Lock()
    release_hold = threading.Event()
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait(timeout=5)
        handle = acquire_distributed_lock(name)
        if handle is None:
            with lock:
                results.append(False)
            return
        with lock:
            results.append(True)
        release_hold.wait(timeout=5)
        handle.release()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        with lock:
            if len(results) == 6:
                break
        time.sleep(0.05)
    release_hold.set()
    for t in threads:
        t.join(timeout=10)
    assert results.count(True) == 1
    assert results.count(False) == 5


def test_pg_advisory_unlocks_when_connection_closes(pg_engine):
    """Simulate pod death: closing the connection must release the advisory lock."""
    name = f"pg-die-{uuid.uuid4().hex}"
    first = acquire_distributed_lock(name)
    assert first is not None
    # Abrupt close without unlock (pod kill / connection drop).
    first.connection.close()
    first.connection = None
    second = acquire_distributed_lock(name)
    assert second is not None
    second.release()
