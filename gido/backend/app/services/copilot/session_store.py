# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Copilot 会话内存存储（Phase 1）。"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.config import settings


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

    def create(self, user_id: int, workspace_id: int, title: str = "新对话") -> str:
        sid = str(uuid.uuid4())
        now = datetime.utcnow()
        with self._lock:
            self._purge_expired()
            self._sessions[sid] = {
                "id": sid,
                "user_id": user_id,
                "workspace_id": workspace_id,
                "title": title[:80] or "新对话",
                "messages": [],
                "created_at": now,
                "updated_at": now,
            }
        return sid

    def get(self, session_id: str, user_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._purge_expired()
            s = self._sessions.get(session_id)
            if not s or s["user_id"] != user_id:
                return None
            return s

    def list_for_user(self, user_id: int, workspace_id: Optional[int] = None) -> List[Dict[str, Any]]:
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
        with self._lock:
            s = self._sessions.get(session_id)
            if not s or s["user_id"] != user_id:
                return False
            del self._sessions[session_id]
            return True


session_store = SessionStore()
