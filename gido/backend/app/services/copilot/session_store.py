# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Copilot 会话存储：生产 Redis 共享，开发环境可回退到进程内。"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.shared_state import key, redis_client, shared_state_required


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _ttl(self) -> timedelta:
        return timedelta(hours=max(settings.COPILOT_SESSION_TTL_HOURS, 1))

    def _purge_expired(self) -> None:
        cutoff = datetime.utcnow() - self._ttl()
        dead = [sid for sid, s in self._sessions.items() if s.get("updated_at", cutoff) < cutoff]
        for sid in dead:
            self._sessions.pop(sid, None)

    def _redis_key(self, session_id: str) -> str:
        return key("copilot", "session", session_id)

    def _redis_index(self, user_id: int) -> str:
        return key("copilot", "user", user_id, "sessions")

    def _ttl_seconds(self) -> int:
        return int(self._ttl().total_seconds())

    def _client(self):
        client = redis_client()
        if client is not None:
            return client
        if shared_state_required():
            raise RuntimeError("多副本共享状态已启用，Copilot 会话不可使用进程内存储")
        return None

    @staticmethod
    def _serialize(session: Dict[str, Any]) -> str:
        payload = dict(session)
        for field in ("created_at", "updated_at"):
            value = payload.get(field)
            if isinstance(value, datetime):
                payload[field] = value.isoformat() + "Z"
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _deserialize(raw: Optional[str]) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        return json.loads(raw)

    def create(self, user_id: int, workspace_id: int, title: str = "新对话") -> str:
        sid = str(uuid.uuid4())
        now = datetime.utcnow()
        session = {
            "id": sid,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "title": title[:80] or "新对话",
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        client = self._client()
        if client is not None:
            with client.pipeline() as pipe:
                pipe.setex(self._redis_key(sid), self._ttl_seconds(), self._serialize(session))
                pipe.zadd(self._redis_index(user_id), {sid: now.timestamp()})
                pipe.expire(self._redis_index(user_id), self._ttl_seconds())
                pipe.execute()
            return sid
        with self._lock:
            self._purge_expired()
            self._sessions[sid] = session
        return sid

    def get(self, session_id: str, user_id: int) -> Optional[Dict[str, Any]]:
        client = self._client()
        if client is not None:
            session = self._deserialize(client.get(self._redis_key(session_id)))
            if not session or int(session["user_id"]) != int(user_id):
                return None
            return session
        with self._lock:
            self._purge_expired()
            s = self._sessions.get(session_id)
            if not s or s["user_id"] != user_id:
                return None
            return s

    def list_for_user(self, user_id: int, workspace_id: Optional[int] = None) -> List[Dict[str, Any]]:
        client = self._client()
        if client is not None:
            index = self._redis_index(user_id)
            session_ids = client.zrevrange(index, 0, -1)
            if not session_ids:
                return []
            raw_sessions = client.mget([self._redis_key(sid) for sid in session_ids])
            out: List[Dict[str, Any]] = []
            stale: List[str] = []
            for sid, raw in zip(session_ids, raw_sessions):
                session = self._deserialize(raw)
                if not session:
                    stale.append(sid)
                    continue
                if workspace_id is not None and int(session["workspace_id"]) != int(workspace_id):
                    continue
                out.append({
                    "id": session["id"],
                    "title": session["title"],
                    "workspace_id": session["workspace_id"],
                    "updated_at": session["updated_at"],
                    "message_count": len(session.get("messages") or []),
                })
            if stale:
                client.zrem(index, *stale)
            return out
        with self._lock:
            self._purge_expired()
            out = []
            for s in self._sessions.values():
                if s["user_id"] != user_id:
                    continue
                if workspace_id is not None and s["workspace_id"] != workspace_id:
                    continue
                out.append({
                    "id": s["id"],
                    "title": s["title"],
                    "workspace_id": s["workspace_id"],
                    "updated_at": s["updated_at"].isoformat() + "Z",
                    "message_count": len(s["messages"]),
                })
            out.sort(key=lambda x: x["updated_at"], reverse=True)
            return out

    def append_messages(self, session_id: str, user_id: int, messages: List[Dict[str, Any]]) -> None:
        client = self._client()
        if client is not None:
            session_key = self._redis_key(session_id)
            with client.pipeline() as pipe:
                while True:
                    try:
                        pipe.watch(session_key)
                        session = self._deserialize(pipe.get(session_key))
                        if not session or int(session["user_id"]) != int(user_id):
                            pipe.unwatch()
                            return
                        session.setdefault("messages", []).extend(messages)
                        now = datetime.utcnow()
                        session["updated_at"] = now
                        if messages and messages[0].get("role") == "user":
                            first = (messages[0].get("content") or "").strip()
                            if first and session["title"] == "新对话":
                                session["title"] = first[:80]
                        pipe.multi()
                        pipe.setex(session_key, self._ttl_seconds(), self._serialize(session))
                        pipe.zadd(self._redis_index(user_id), {session_id: now.timestamp()})
                        pipe.expire(self._redis_index(user_id), self._ttl_seconds())
                        pipe.execute()
                        return
                    except Exception as ex:
                        from redis.exceptions import WatchError

                        if isinstance(ex, WatchError):
                            continue
                        raise
        with self._lock:
            s = self._sessions.get(session_id)
            if not s or s["user_id"] != user_id:
                return
            s["messages"].extend(messages)
            s["updated_at"] = datetime.utcnow()
            if messages and messages[0].get("role") == "user":
                first = (messages[0].get("content") or "").strip()
                if first and s["title"] == "新对话":
                    s["title"] = first[:80]

    def delete(self, session_id: str, user_id: int) -> bool:
        client = self._client()
        if client is not None:
            session = self._deserialize(client.get(self._redis_key(session_id)))
            if not session or int(session["user_id"]) != int(user_id):
                return False
            with client.pipeline() as pipe:
                pipe.delete(self._redis_key(session_id))
                pipe.zrem(self._redis_index(user_id), session_id)
                pipe.execute()
            return True
        with self._lock:
            s = self._sessions.get(session_id)
            if not s or s["user_id"] != user_id:
                return False
            del self._sessions[session_id]
            return True


session_store = SessionStore()
