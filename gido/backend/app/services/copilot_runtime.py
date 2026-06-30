# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Copilot LLM 运行时配置：工作空间覆盖 > 全局平台集成 > 环境变量。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.workspace import PlatformIntegration, WorkspacePlatformIntegration
from app.services.ds_runtime import ensure_platform_integration_row
from app.services.workspace_settings import ensure_workspace_platform_row


@dataclass
class CopilotRuntimeConfig:
    base_url: str
    model: str
    api_key: str
    source: str  # workspace | global | environment


def _merge_copilot_from_row(
    *,
    base_url: str,
    model: str,
    api_key: str,
    row: Optional[object],
) -> tuple[str, str, str]:
    if row is None:
        return base_url, model, api_key
    if getattr(row, "copilot_llm_base_url", None) and str(row.copilot_llm_base_url).strip():
        base_url = str(row.copilot_llm_base_url).strip()
    if getattr(row, "copilot_llm_model", None) and str(row.copilot_llm_model).strip():
        model = str(row.copilot_llm_model).strip()
    if getattr(row, "copilot_llm_api_key", None) is not None and str(row.copilot_llm_api_key).strip():
        api_key = str(row.copilot_llm_api_key).strip()
    return base_url, model, api_key


def get_copilot_runtime(db: Session, workspace_id: Optional[int] = None) -> CopilotRuntimeConfig:
    base_url = (settings.COPILOT_LLM_BASE_URL or "").strip()
    model = (settings.COPILOT_LLM_MODEL or "qwen-max").strip()
    api_key = (settings.COPILOT_LLM_API_KEY or "").strip()
    source = "environment"

    global_row = db.query(PlatformIntegration).filter(PlatformIntegration.id == 1).first()
    base_url, model, api_key = _merge_copilot_from_row(
        base_url=base_url, model=model, api_key=api_key, row=global_row
    )
    if global_row and (
        (global_row.copilot_llm_base_url and str(global_row.copilot_llm_base_url).strip())
        or (global_row.copilot_llm_model and str(global_row.copilot_llm_model).strip())
        or (global_row.copilot_llm_api_key and str(global_row.copilot_llm_api_key).strip())
    ):
        source = "global"

    if workspace_id is not None:
        ws_row = (
            db.query(WorkspacePlatformIntegration)
            .filter(WorkspacePlatformIntegration.workspace_id == workspace_id)
            .first()
        )
        if ws_row and (
            (ws_row.copilot_llm_base_url and str(ws_row.copilot_llm_base_url).strip())
            or (ws_row.copilot_llm_model and str(ws_row.copilot_llm_model).strip())
            or (ws_row.copilot_llm_api_key and str(ws_row.copilot_llm_api_key).strip())
        ):
            base_url, model, api_key = _merge_copilot_from_row(
                base_url=base_url, model=model, api_key=api_key, row=ws_row
            )
            source = "workspace"

    return CopilotRuntimeConfig(
        base_url=base_url.rstrip("/"),
        model=model,
        api_key=api_key,
        source=source,
    )


def ensure_global_platform_row(db: Session) -> PlatformIntegration:
    return ensure_platform_integration_row(db)


def ensure_ws_platform_row(db: Session, workspace_id: int) -> WorkspacePlatformIntegration:
    return ensure_workspace_platform_row(db, workspace_id)


def test_copilot_runtime(cfg: CopilotRuntimeConfig) -> Dict[str, Any]:
    from app.services.copilot.llm_client import LlmClient

    if not cfg.api_key:
        return {"ok": False, "message": "未配置 API Key"}
    client = LlmClient(base_url=cfg.base_url, model=cfg.model, api_key=cfg.api_key)
    try:
        client.chat_completion([{"role": "user", "content": "请回复 OK"}], temperature=0)
        return {"ok": True, "message": f"模型 {cfg.model} 连通正常"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:800]}
