# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import pytest

from app.services.copilot.session_store import SessionStore


def test_session_store_local_fallback(monkeypatch):
    monkeypatch.setattr("app.services.copilot.session_store.settings.SHARED_STATE_REQUIRED", False)
    monkeypatch.setattr("app.services.copilot.session_store.redis_client", lambda: None)
    store = SessionStore()

    session_id = store.create(7, 11)
    store.append_messages(
        session_id,
        7,
        [
            {"role": "user", "content": "生产高可用"},
            {"role": "assistant", "content": "已处理"},
        ],
    )

    session = store.get(session_id, 7)
    assert session is not None
    assert session["title"] == "生产高可用"
    assert len(session["messages"]) == 2
    assert store.get(session_id, 8) is None
    assert store.list_for_user(7, 11)[0]["message_count"] == 2
    assert store.delete(session_id, 7) is True
    assert store.get(session_id, 7) is None


def test_session_store_fail_closed_when_shared_required(monkeypatch):
    monkeypatch.setattr("app.services.copilot.session_store.settings.SHARED_STATE_REQUIRED", True)
    monkeypatch.setattr("app.services.copilot.session_store.redis_client", lambda: None)
    store = SessionStore()
    with pytest.raises(RuntimeError, match="不可使用进程内存储"):
        store.create(1, 2)


class _FakeRedis:
    def __init__(self):
        self._kv = {}
        self._zsets = {}

    def pipeline(self):
        return _FakePipeline(self)

    def get(self, key):
        return self._kv.get(key)

    def mget(self, keys):
        return [self._kv.get(k) for k in keys]

    def zrevrange(self, key, start, end):
        items = sorted(self._zsets.get(key, {}).items(), key=lambda x: x[1], reverse=True)
        return [k for k, _ in items]

    def zrem(self, key, *members):
        z = self._zsets.setdefault(key, {})
        for m in members:
            z.pop(m, None)


class _FakePipeline:
    def __init__(self, client: _FakeRedis):
        self.client = client
        self._ops = []
        self._watching = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def setex(self, key, ttl, value):
        self._ops.append(("setex", key, value))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def delete(self, key):
        self._ops.append(("delete", key))
        return self

    def zrem(self, key, member):
        self._ops.append(("zrem", key, member))
        return self

    def watch(self, key):
        self._watching = key

    def unwatch(self):
        self._watching = None

    def multi(self):
        return self

    def get(self, key):
        return self.client.get(key)

    def execute(self):
        for op in self._ops:
            kind = op[0]
            if kind == "setex":
                self.client._kv[op[1]] = op[2]
            elif kind == "zadd":
                self.client._zsets.setdefault(op[1], {}).update(op[2])
            elif kind == "delete":
                self.client._kv.pop(op[1], None)
            elif kind == "zrem":
                self.client._zsets.setdefault(op[1], {}).pop(op[2], None)
        self._ops = []
        self._watching = None
        return []


def test_session_store_redis_path(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("app.services.copilot.session_store.settings.SHARED_STATE_REQUIRED", True)
    monkeypatch.setattr("app.services.copilot.session_store.redis_client", lambda: fake)
    store = SessionStore()
    sid = store.create(3, 9)
    store.append_messages(sid, 3, [{"role": "user", "content": "hello redis"}])
    session = store.get(sid, 3)
    assert session is not None
    assert session["title"] == "hello redis"
    assert len(session["messages"]) == 1
    assert store.list_for_user(3, 9)[0]["id"] == sid
    assert store.delete(sid, 3) is True
