# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""探查查询树：校验后写入 JSON。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

_MAX_FOLDERS = 200
_MAX_SCRIPTS = 400
_MAX_SQL_CHARS = 500_000
_MAX_NAME = 128


def sanitize_probe_tree_state(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("探查状态须为对象")
    folders_in = raw.get("folders") if isinstance(raw.get("folders"), list) else []
    scripts_in = raw.get("scripts") if isinstance(raw.get("scripts"), list) else []
    if len(folders_in) > _MAX_FOLDERS:
        raise ValueError(f"目录数超过 {_MAX_FOLDERS}")
    if len(scripts_in) > _MAX_SCRIPTS:
        raise ValueError(f"查询数超过 {_MAX_SCRIPTS}")

    folders: List[dict] = []
    for f in folders_in:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or "").strip()
        name = str(f.get("name") or "").strip()[:_MAX_NAME]
        if not fid or not name:
            continue
        parent = f.get("parentId")
        folders.append({
            "id": fid,
            "name": name,
            "parentId": None if parent in (None, "", "null") else str(parent),
            "sort_order": int(f.get("sort_order") or 0),
        })

    scripts: List[dict] = []
    for s in scripts_in:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        name = str(s.get("name") or "").strip()[:_MAX_NAME]
        if not sid or not name:
            continue
        sql = str(s.get("sql") or "")
        if len(sql) > _MAX_SQL_CHARS:
            sql = sql[:_MAX_SQL_CHARS]
        folder_id = s.get("folderId")
        ds = s.get("datasource_id")
        try:
            ds_id = int(ds) if ds is not None and str(ds).strip() != "" else None
        except (TypeError, ValueError):
            ds_id = None
        try:
            lim = int(s.get("limit") or 10000)
        except (TypeError, ValueError):
            lim = 10000
        lim = min(max(lim, 1), 10000)
        scripts.append({
            "id": sid,
            "name": name,
            "folderId": None if folder_id in (None, "", "null") else str(folder_id),
            "sql": sql,
            "datasource_id": ds_id,
            "limit": lim,
            "resultColMeta": s.get("resultColMeta") if isinstance(s.get("resultColMeta"), dict) else None,
            "sort_order": int(s.get("sort_order") or 0),
        })

    if not scripts:
        raise ValueError("至少保留一条探查查询")

    active = raw.get("activeScriptId")
    active_s = str(active) if active else ""
    if not any(s["id"] == active_s for s in scripts):
        active_s = scripts[0]["id"]

    return {"folders": folders, "scripts": scripts, "activeScriptId": active_s}
