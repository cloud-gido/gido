# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""玑渡 Copilot API。"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import User
from app.services.copilot.orchestrator import CopilotOrchestrator
from app.services.copilot.session_store import session_store

router = APIRouter(prefix="/copilot", tags=["玑渡 Copilot"])

_rate_lock = threading.Lock()
_rate_buckets: Dict[int, List[float]] = {}


class CopilotChatIn(BaseModel):
    workspace_id: int
    message: str = Field(min_length=1, max_length=8000)
    session_id: Optional[str] = None
    datasource_id: Optional[int] = None
    stream: bool = False


def _check_rate_limit(user_id: int) -> None:
    limit = max(settings.COPILOT_RATE_LIMIT_PER_MINUTE, 1)
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(user_id, [])
        bucket[:] = [t for t in bucket if now - t < 60]
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="Copilot 请求过于频繁，请稍后再试")
        bucket.append(now)


def _require_configured() -> None:
    if not settings.copilot_configured:
        raise HTTPException(
            status_code=503,
            detail="Copilot 尚未配置 LLM API Key，请联系管理员在环境变量 COPILOT_LLM_API_KEY 或 Doppler 中配置",
        )


@router.get("/status")
def copilot_status(current_user: User = Depends(get_current_user)):
    configured = settings.copilot_configured
    return {
        "configured": configured,
        "model": settings.COPILOT_LLM_MODEL,
        "base_url": settings.COPILOT_LLM_BASE_URL,
        "message": None if configured else "请联系管理员配置 COPILOT_LLM_API_KEY（通义 DashScope 或 OpenAI 兼容端点）",
    }


@router.get("/sessions")
def list_sessions(
    workspace_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
):
    return {"sessions": session_store.list_for_user(current_user.id, workspace_id)}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, current_user: User = Depends(get_current_user)):
    s = session_store.get(session_id, current_user.id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "id": s["id"],
        "title": s["title"],
        "workspace_id": s["workspace_id"],
        "messages": s["messages"],
    }


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, current_user: User = Depends(get_current_user)):
    if not session_store.delete(session_id, current_user.id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.post("/chat")
def copilot_chat(
    body: CopilotChatIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _check_rate_limit(current_user.id)
    _require_configured()
    orch = CopilotOrchestrator()
    if body.stream:
        gen = orch.run_chat_stream(
            db,
            current_user,
            body.workspace_id,
            body.message.strip(),
            body.session_id,
            body.datasource_id,
        )
        return StreamingResponse(gen, media_type="text/event-stream")

    _, payload = orch.run_chat(
        db,
        current_user,
        body.workspace_id,
        body.message.strip(),
        body.session_id,
        body.datasource_id,
    )
    return payload
