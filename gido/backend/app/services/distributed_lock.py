"""Cross-replica locks backed by PostgreSQL advisory locks."""
from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from typing import Iterator

from app.core.database import engine

_local_guard = threading.Lock()
_local_locks: dict[str, threading.Lock] = {}


class DistributedLockHandle:
    def __init__(self, name: str, connection=None, local_lock: threading.Lock | None = None) -> None:
        self.name = name
        self.connection = connection
        self.local_lock = local_lock

    def release(self) -> None:
        if self.connection is not None:
            try:
                cursor = self.connection.cursor()
                cursor.execute("SELECT pg_advisory_unlock(%s)", (_lock_id(self.name),))
            finally:
                self.connection.close()
                self.connection = None
        if self.local_lock is not None:
            self.local_lock.release()
            self.local_lock = None


def _lock_id(name: str) -> int:
    value = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=False)
    return value if value < 2**63 else value - 2**64


def acquire_distributed_lock(name: str, *, blocking: bool = False) -> DistributedLockHandle | None:
    if engine.dialect.name == "postgresql":
        connection = engine.raw_connection()
        cursor = connection.cursor()
        function_name = "pg_advisory_lock" if blocking else "pg_try_advisory_lock"
        cursor.execute(f"SELECT {function_name}(%s)", (_lock_id(name),))
        if not blocking and not bool(cursor.fetchone()[0]):
            connection.close()
            return None
        if blocking:
            cursor.fetchone()
        return DistributedLockHandle(name, connection=connection)

    with _local_guard:
        local = _local_locks.setdefault(name, threading.Lock())
    if not local.acquire(blocking=blocking):
        return None
    return DistributedLockHandle(name, local_lock=local)


@contextmanager
def try_distributed_lock(name: str) -> Iterator[bool]:
    """
    Hold a session-level PostgreSQL advisory lock for the context lifetime.

    SQLite/local tests use a process lock; production PostgreSQL releases the
    lock automatically if the pod or DB connection dies.
    """
    handle = acquire_distributed_lock(name)
    try:
        yield handle is not None
    finally:
        if handle is not None:
            handle.release()
