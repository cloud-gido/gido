# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""DolphinScheduler 运行时配置：工作空间覆盖 > 全局平台集成 > 环境变量。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.workspace import PlatformIntegration, WorkspacePlatformIntegration


@dataclass
class DolphinRuntimeConfig:
    enabled: bool
    url: str
    ui_url: Optional[str]
    token: str
    project_name: str


def _get_global_row(db: Session) -> Optional[PlatformIntegration]:
    return db.query(PlatformIntegration).filter(PlatformIntegration.id == 1).first()


def _get_workspace_row(db: Session, workspace_id: int) -> Optional[WorkspacePlatformIntegration]:
    return (
        db.query(WorkspacePlatformIntegration)
        .filter(WorkspacePlatformIntegration.workspace_id == workspace_id)
        .first()
    )


def normalize_ds_api_base(raw: str) -> str:
    s = (raw or "").strip().rstrip("/")
    if not s:
        return s
    if s.lower().endswith("/ui"):
        s = s[:-3].rstrip("/")
    return s


def _running_in_docker() -> bool:
    return os.path.exists("/.dockerenv")


def resolve_ds_url_for_backend_http(url: str) -> str:
    u = (url or "").strip()
    if not u or not _running_in_docker():
        return u
    try:
        p = urlparse(u)
        host = (p.hostname or "").lower()
        if host not in ("localhost", "127.0.0.1"):
            return u
        port = p.port
        new_netloc = f"host.docker.internal:{port}" if port else "host.docker.internal"
        return urlunparse((p.scheme, new_netloc, p.path or "", p.params, p.query, p.fragment))
    except Exception:
        return u


def ensure_platform_integration_row(db: Session) -> PlatformIntegration:
    row = _get_global_row(db)
    if row:
        return row
    row = PlatformIntegration(id=1)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_workspace_platform_integration_row(db: Session, workspace_id: int) -> WorkspacePlatformIntegration:
    from app.services.workspace_settings import ensure_workspace_platform_row

    return ensure_workspace_platform_row(db, workspace_id)


def _merge_ds_from_row(
    *,
    enabled: bool,
    url: str,
    ui: Optional[str],
    token: str,
    project: str,
    row: Optional[object],
) -> tuple[bool, str, Optional[str], str, str]:
    if row is None:
        return enabled, url, ui, token, project
    if getattr(row, "ds_enabled", None) is not None:
        enabled = bool(row.ds_enabled)
    if getattr(row, "ds_url", None) and str(row.ds_url).strip():
        url = str(row.ds_url).strip()
    if getattr(row, "ds_ui_url", None) is not None:
        ui = str(row.ds_ui_url).strip() if str(row.ds_ui_url).strip() else None
    if getattr(row, "ds_token", None) is not None and str(row.ds_token).strip():
        token = str(row.ds_token).strip()
    if getattr(row, "ds_project_name", None) and str(row.ds_project_name).strip():
        project = str(row.ds_project_name).strip()
    return enabled, url, ui, token, project


def get_dolphin_runtime(db: Session, workspace_id: Optional[int] = None) -> DolphinRuntimeConfig:
    enabled = settings.DS_ENABLED
    url = (settings.DS_URL or "").strip()
    ui = (settings.DS_UI_URL or "").strip() or None
    token = (settings.DS_TOKEN or "").strip()
    project = (settings.DS_PROJECT_NAME or "GIDO").strip()

    global_row = _get_global_row(db)
    enabled, url, ui, token, project = _merge_ds_from_row(
        enabled=enabled, url=url, ui=ui, token=token, project=project, row=global_row
    )

    if workspace_id is not None:
        ws_row = _get_workspace_row(db, int(workspace_id))
        enabled, url, ui, token, project = _merge_ds_from_row(
            enabled=enabled, url=url, ui=ui, token=token, project=project, row=ws_row
        )

    url = normalize_ds_api_base(url)
    return DolphinRuntimeConfig(enabled=enabled, url=url, ui_url=ui, token=token, project_name=project)


def refresh_ds_client(db: Session, workspace_id: Optional[int] = None) -> None:
    cfg = get_dolphin_runtime(db, workspace_id)
    from app.services.dolphin import ds_client

    connect_url = resolve_ds_url_for_backend_http(cfg.url)
    ds_client.apply_runtime(connect_url, cfg.token, cfg.project_name)
