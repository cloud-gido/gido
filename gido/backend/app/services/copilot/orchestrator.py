# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Copilot 对话编排。"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.workspace import User
from app.services.audit import log_action
from app.services.copilot.llm_client import LlmClient
from app.services.copilot.prompts import SYSTEM_PROMPT
from app.services.copilot.session_store import session_store
from app.services.copilot.tools import TOOL_DEFINITIONS, run_tool, tool_result_for_llm
from app.services.copilot_runtime import CopilotRuntimeConfig, get_copilot_runtime


class CopilotOrchestrator:
    def __init__(self, runtime: Optional[CopilotRuntimeConfig] = None) -> None:
        if runtime:
            self.llm = LlmClient(
                base_url=runtime.base_url,
                model=runtime.model,
                api_key=runtime.api_key,
            )
            self._runtime = runtime
        else:
            self.llm = LlmClient()
            self._runtime = None

    def _build_messages(self, history: List[Dict[str, Any]], user_message: str, datasource_id: Optional[int]) -> List[Dict[str, Any]]:
        ctx = f"当前工作空间已选数据源 ID: {datasource_id}" if datasource_id else "当前未选择数据源，执行 SQL 前请提示用户选择"
        return [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + ctx},
            *history,
            {"role": "user", "content": user_message},
        ]

    def run_chat(
        self,
        db: Session,
        user: User,
        workspace_id: int,
        user_message: str,
        session_id: Optional[str],
        datasource_id: Optional[int],
    ) -> Tuple[str, Dict[str, Any]]:
        sid = session_id or session_store.create(user.id, workspace_id)
        session = session_store.get(sid, user.id)
        if not session:
            sid = session_store.create(user.id, workspace_id)
            session = session_store.get(sid, user.id)

        history = list(session.get("messages") or [])
        messages = self._build_messages(history, user_message, datasource_id)

        tool_trace: List[Dict[str, Any]] = []
        query_result: Optional[Dict[str, Any]] = None
        last_sql: Optional[str] = None
        t0 = time.time()

        for _ in range(max(settings.COPILOT_MAX_TOOL_ROUNDS, 1)):
            data = self.llm.chat_completion(messages, tools=TOOL_DEFINITIONS)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            tool_calls = self.llm.parse_tool_calls(choice)

            if not tool_calls:
                assistant_text = self.llm.message_content(msg)
                new_msgs = [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_text},
                ]
                session_store.append_messages(sid, user.id, new_msgs)
                log_action(
                    db,
                    user.id,
                    "copilot.chat",
                    "copilot",
                    workspace_id=workspace_id,
                    detail={
                        "session_id": sid,
                        "question": user_message[:500],
                        "sql": last_sql,
                        "model": self.llm.model,
                        "latency_ms": int((time.time() - t0) * 1000),
                        "tool_calls": len(tool_trace),
                    },
                )
                return sid, {
                    "session_id": sid,
                    "message": assistant_text,
                    "sql": last_sql,
                    "query_result": query_result,
                    "tool_trace": tool_trace,
                }

            messages.append(msg)
            for call in tool_calls:
                name = call.get("name") or ""
                args = call.get("arguments") or {}
                raw = run_tool(db, user, workspace_id, datasource_id, name, args)
                if name == "run_readonly_sql" and not raw.get("error"):
                    last_sql = raw.get("sql")
                    query_result = raw
                llm_payload = tool_result_for_llm(name, raw)
                tool_trace.append({"name": name, "arguments": args, "result_summary": llm_payload})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(llm_payload, ensure_ascii=False),
                })

        assistant_text = "已达到工具调用次数上限，请简化问题后重试。"
        session_store.append_messages(sid, user.id, [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_text},
        ])
        return sid, {
            "session_id": sid,
            "message": assistant_text,
            "sql": last_sql,
            "query_result": query_result,
            "tool_trace": tool_trace,
        }

    def run_chat_stream(
        self,
        db: Session,
        user: User,
        workspace_id: int,
        user_message: str,
        session_id: Optional[str],
        datasource_id: Optional[int],
    ) -> Generator[str, None, None]:
        """SSE：先 status，再一次性返回结果（Phase 1 简化流式）。"""
        yield _sse("status", {"phase": "thinking"})
        try:
            sid, payload = self.run_chat(db, user, workspace_id, user_message, session_id, datasource_id)
            text = payload.get("message") or ""
            chunk_size = 12
            for i in range(0, len(text), chunk_size):
                yield _sse("delta", {"content": text[i : i + chunk_size]})
            yield _sse("done", payload)
        except Exception as e:
            yield _sse("error", {"detail": str(e)[:2000]})


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
