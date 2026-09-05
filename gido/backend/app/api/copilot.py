# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""玑渡 Copilot API。"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import User
from app.services.copilot.orchestrator import CopilotOrchestrator
from app.services.copilot.session_store import session_store
from app.services.copilot_runtime import get_copilot_runtime
from app.services.rbac import assert_workspace_access
from app.services.shared_state import rate_limit_hit

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
    try:
        exceeded = rate_limit_hit(f"copilot:{user_id}", limit, 60)
    except RuntimeError as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex
    if exceeded is not None:
        if exceeded:
            raise HTTPException(status_code=429, detail="Copilot 请求过于频繁，请稍后再试")
        return
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(user_id, [])
        bucket[:] = [t for t in bucket if now - t < 60]
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="Copilot 请求过于频繁，请稍后再试")
        bucket.append(now)


def _require_configured(db: Session, workspace_id: Optional[int]) -> None:
    cfg = get_copilot_runtime(db, workspace_id)
    if not cfg.api_key:
        raise HTTPException(
            status_code=503,
            detail="Copilot 尚未配置 LLM API Key，请在「空间设置 → Copilot」或「系统管理 → 平台集成」中配置",
        )


@router.get("/status")
def copilot_status(
    workspace_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if workspace_id is not None:
        assert_workspace_access(db, current_user, int(workspace_id))
    cfg = get_copilot_runtime(db, workspace_id)
    configured = bool(cfg.api_key)
    return {
        "configured": configured,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "source": cfg.source,
        "message": None if configured else "请在空间设置或平台集成中配置 Copilot LLM API Key",
    }


def _session_store_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except RuntimeError as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex


@router.get("/sessions")
def list_sessions(
    workspace_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if workspace_id is not None:
        assert_workspace_access(db, current_user, int(workspace_id))
    return {"sessions": _session_store_call(session_store.list_for_user, current_user.id, workspace_id)}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, current_user: User = Depends(get_current_user)):
    s = _session_store_call(session_store.get, session_id, current_user.id)
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
    if not _session_store_call(session_store.delete, session_id, current_user.id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.post("/chat")
def copilot_chat(
    body: CopilotChatIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_access(db, current_user, int(body.workspace_id))
    _check_rate_limit(current_user.id)
    _require_configured(db, body.workspace_id)
    runtime = get_copilot_runtime(db, body.workspace_id)
    orch = CopilotOrchestrator(runtime)
    try:
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
    except RuntimeError as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex
