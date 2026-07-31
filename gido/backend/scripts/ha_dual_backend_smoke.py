#!/usr/bin/env python3
# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Staging / local HA smoke for multi-replica backend.

Checks (always when Redis reachable):
  1. claim_once atomicity against shared Redis (simulates two pods racing)
  2. Copilot SessionStore visibility across two store instances

Optional (set env):
  GIDO_HA_SMOKE_BACKENDS=http://backend-a:8001,http://backend-b:8001
      → GET /health and /ready on each URL

  GIDO_TEST_REDIS_URL / REDIS_URL
      → Redis used for claim/session checks (default redis://127.0.0.1:6379/15)

  GIDO_TEST_DATABASE_URL / DATABASE_URL (postgresql://...)
      → cross-connection advisory lock check

Exit 0 on success; non-zero with a short failure list on stderr.
"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from typing import List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Allow `python scripts/ha_dual_backend_smoke.py` from backend root or repo root.
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


def _http_json(url: str, timeout: float = 5.0) -> tuple[int, str]:
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace") if ex.fp else ""
        return ex.code, body
    except URLError as ex:
        return 0, str(ex.reason)


def check_backends(urls: List[str]) -> List[str]:
    errors: List[str] = []
    for base in urls:
        base = base.rstrip("/")
        for path in ("/health", "/ready"):
            code, body = _http_json(f"{base}{path}")
            if path == "/health" and code != 200:
                errors.append(f"{base}{path} -> HTTP {code}: {body[:200]}")
            if path == "/ready" and code != 200:
                errors.append(f"{base}{path} -> HTTP {code}: {body[:200]}")
    return errors


def check_redis_claim_and_session() -> List[str]:
    from app.services import shared_state
    from app.services.copilot.session_store import SessionStore
    from tests.ha_helpers import redis_reachable, redis_test_url

    url = redis_test_url()
    if not redis_reachable(url):
        return [f"Redis not reachable at {url} (set GIDO_TEST_REDIS_URL)"]

    shared_state.settings.SHARED_STATE_REQUIRED = True
    shared_state.settings.REDIS_URL = url
    shared_state.settings.REDIS_PASSWORD = (os.environ.get("REDIS_PASSWORD") or "").strip()
    shared_state.settings.SHARED_STATE_PREFIX = f"gido-smoke-{uuid.uuid4().hex[:8]}"
    shared_state.reset_redis_client_for_tests()

    errors: List[str] = []
    claim = f"smoke-claim-{uuid.uuid4().hex}"
    results: List[bool | None] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait(timeout=5)
        results.append(shared_state.claim_once(claim, 60))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    if results.count(True) != 1 or results.count(False) != 7:
        errors.append(f"claim_once race failed: winners={results.count(True)} results={results}")

    a = SessionStore()
    b = SessionStore()
    sid = a.create(901, 1)
    a.append_messages(sid, 901, [{"role": "user", "content": "pod-a-write"}])
    session = b.get(sid, 901)
    if not session or session.get("title") != "pod-a-write":
        errors.append(f"session not visible across stores: {session!r}")
    elif not (session.get("messages") or []):
        errors.append("session messages empty on second store")
    b.delete(sid, 901)
    shared_state.reset_redis_client_for_tests()
    return errors


def check_pg_advisory() -> List[str]:
    from app.services import distributed_lock
    from app.services.distributed_lock import acquire_distributed_lock
    from tests.ha_helpers import pg_reachable, pg_test_url

    url = pg_test_url()
    if not url:
        return []  # optional
    if not pg_reachable(url):
        return [f"PostgreSQL configured but not reachable: {url}"]

    from sqlalchemy import create_engine

    engine = create_engine(url, pool_pre_ping=True, pool_size=4)
    distributed_lock.engine = engine
    name = f"smoke-lock-{uuid.uuid4().hex}"
    errors: List[str] = []
    first = acquire_distributed_lock(name)
    if first is None:
        engine.dispose()
        return ["failed to acquire first advisory lock"]
    second = acquire_distributed_lock(name)
    if second is not None:
        second.release()
        errors.append("second connection unexpectedly acquired same advisory lock")
    first.release()
    again = acquire_distributed_lock(name)
    if again is None:
        errors.append("lock not reusable after release")
    else:
        again.release()
    engine.dispose()
    return errors


def main() -> int:
    errors: List[str] = []
    backends = [
        u.strip()
        for u in (os.environ.get("GIDO_HA_SMOKE_BACKENDS") or "").split(",")
        if u.strip()
    ]
    if backends:
        print(f"Checking backends: {backends}")
        errors.extend(check_backends(backends))
    else:
        print("Skip HTTP backend checks (set GIDO_HA_SMOKE_BACKENDS=url1,url2)")

    print("Checking Redis claim_once + SessionStore cross-visibility...")
    errors.extend(check_redis_claim_and_session())

    print("Checking PostgreSQL advisory lock (optional)...")
    errors.extend(check_pg_advisory())

    if errors:
        print("HA smoke FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("HA smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
