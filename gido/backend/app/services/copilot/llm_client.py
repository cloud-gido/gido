# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""OpenAI 兼容 LLM 客户端。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings


class LlmClient:
    def __init__(self) -> None:
        self.base_url = settings.COPILOT_LLM_BASE_URL.rstrip("/")
        self.model = settings.COPILOT_LLM_MODEL
        self.api_key = (settings.COPILOT_LLM_API_KEY or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        if not self.configured:
            raise RuntimeError("Copilot LLM 未配置 API Key")
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            if resp.status_code >= 400:
                detail = resp.text[:2000]
                try:
                    detail = resp.json().get("error", {}).get("message", detail)
                except Exception:
                    pass
                raise RuntimeError(f"LLM 请求失败 ({resp.status_code}): {detail}")
            return resp.json()

    @staticmethod
    def message_content(msg: Dict[str, Any]) -> str:
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text") or "")
            return "".join(parts)
        return str(content or "")

    @staticmethod
    def parse_tool_calls(choice: Dict[str, Any]) -> List[Dict[str, Any]]:
        msg = choice.get("message") or {}
        calls = msg.get("tool_calls") or []
        out = []
        for c in calls:
            fn = c.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {}
            out.append({
                "id": c.get("id"),
                "name": fn.get("name"),
                "arguments": args if isinstance(args, dict) else {},
            })
        return out
