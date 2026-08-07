# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务 API 配置包：跨环境导出 / 导入（类似 API 网关配置迁移）。

设计要点（业界常见做法）：
- 稳定身份用 api_code，不用数字 id
- 数据源按 name 引用，导入时可 remap
- 导入一律落到 draft，需再次发布才上线（避免静默改生产）
- schema_version 做前向兼容：忽略未知字段
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models.data_service import DataApi, DataApiParam
from app.models.workspace import DataSource
from app.services.data_api_engine import wizard_to_sql

BUNDLE_FORMAT = "gido.serve.api_bundle"
BUNDLE_SCHEMA_VERSION = 1
_API_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _param_out(p: DataApiParam) -> dict:
    return {
        "name": p.name,
        "param_in": p.param_in or "query",
        "data_type": p.data_type or "string",
        "required": bool(p.required),
        "default_value": p.default_value,
        "description": p.description,
        "validator_regex": p.validator_regex,
        "sort_order": int(p.sort_order or 0),
    }


def _param_ns(params: List[dict]) -> list:
    return [SimpleNamespace(name=p["name"]) for p in params]


def _maybe_compile_wizard_sql(api: DataApi, params: List[dict]) -> None:
    if api.mode == "wizard" and api.wizard_config:
        api.sql_template = wizard_to_sql(api.wizard_config or {}, _param_ns(params))  # type: ignore[arg-type]


def api_to_bundle_item(api: DataApi, ds_name: Optional[str] = None, ds_type: Optional[str] = None) -> dict:
    """单条 API → 可移植配置（不含运行态）。"""
    return {
        "api_code": api.api_code,
        "name": api.name,
        "description": api.description,
        "mode": api.mode or "sql",
        "http_method": (api.http_method or "GET").upper(),
        "datasource_ref": {
            "by": "name",
            "value": ds_name,
            "ds_type": ds_type,
        }
        if ds_name or ds_type
        else None,
        "sql_template": api.sql_template,
        "wizard_config": api.wizard_config,
        "response_fields": api.response_fields,
        "pagination_enabled": bool(api.pagination_enabled),
        "page_size_default": int(api.page_size_default or 20),
        "page_size_max": int(api.page_size_max or 1000),
        "timeout_seconds": int(api.timeout_seconds or 30),
        "cache_ttl_seconds": int(api.cache_ttl_seconds or 0),
        "max_rows": int(api.max_rows or 10000),
        "params": [_param_out(p) for p in sorted(api.params or [], key=lambda x: x.sort_order or 0)],
        # 仅作溯源，导入忽略
        "source_status": api.status,
        "source_version": api.version,
    }


def export_api_bundle(
    db: Session,
    *,
    workspace_id: int,
    api_ids: Optional[List[int]] = None,
    api_codes: Optional[List[str]] = None,
) -> dict:
    q = (
        db.query(DataApi)
        .options(joinedload(DataApi.params))
        .filter(DataApi.workspace_id == workspace_id)
    )
    if api_ids:
        q = q.filter(DataApi.id.in_([int(i) for i in api_ids]))
    if api_codes:
        codes = [str(c).strip().lower() for c in api_codes if str(c).strip()]
        q = q.filter(DataApi.api_code.in_(codes))
    rows = q.order_by(DataApi.api_code.asc()).all()
    if not rows:
        raise HTTPException(status_code=404, detail="未找到可导出的 API")

    ds_ids = {r.datasource_id for r in rows if r.datasource_id}
    ds_rows = db.query(DataSource).filter(DataSource.id.in_(ds_ids)).all() if ds_ids else []
    ds_meta = {d.id: (d.name, d.ds_type) for d in ds_rows}

    return {
        "format": BUNDLE_FORMAT,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "exported_at": _utcnow_iso(),
        "source_workspace_id": workspace_id,
        "apis": [
            api_to_bundle_item(
                r,
                (ds_meta.get(r.datasource_id) or (None, None))[0],
                (ds_meta.get(r.datasource_id) or (None, None))[1],
            )
            for r in rows
        ],
    }


def _normalize_bundle(raw: Any) -> Tuple[int, List[dict]]:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="配置包须为 JSON 对象")
    fmt = raw.get("format")
    if fmt and fmt != BUNDLE_FORMAT:
        raise HTTPException(status_code=400, detail=f"不支持的配置包 format: {fmt}")
    ver = int(raw.get("schema_version") or 1)
    if ver > BUNDLE_SCHEMA_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"配置包 schema_version={ver} 高于当前支持的 {BUNDLE_SCHEMA_VERSION}，请升级平台后再导入",
        )
    apis = raw.get("apis")
    if not isinstance(apis, list) or not apis:
        raise HTTPException(status_code=400, detail="配置包 apis 不能为空")
    return ver, apis


def _workspace_datasources(db: Session, workspace_id: int) -> List[DataSource]:
    return (
        db.query(DataSource)
        .filter(DataSource.workspace_id == workspace_id)
        .order_by(DataSource.id.asc())
        .all()
    )


def _resolve_datasource_id(
    db: Session,
    workspace_id: int,
    item: dict,
    datasource_map: Dict[str, str],
) -> Optional[int]:
    """一键导入时自动绑数据源，优先同名，其次同类型唯一，再回退空间默认。

    人工 datasource_map 仅在多源歧义时才需要。
    """
    from app.models.workspace import Workspace

    ref = item.get("datasource_ref") if isinstance(item.get("datasource_ref"), dict) else {}
    name: Optional[str] = None
    ds_type: Optional[str] = None
    if ref.get("value"):
        name = str(ref.get("value")).strip()
    elif item.get("datasource_name"):
        name = str(item.get("datasource_name")).strip()
    if ref.get("ds_type"):
        ds_type = str(ref.get("ds_type")).strip().lower()
    elif item.get("datasource_type"):
        ds_type = str(item.get("datasource_type")).strip().lower()

    rows = _workspace_datasources(db, workspace_id)
    active = [d for d in rows if getattr(d, "is_active", True) is not False] or rows

    # 1) 显式映射（可选）
    if name:
        mapped = datasource_map.get(name) or datasource_map.get(name.lower())
        target_name = (mapped or name).strip()
        ds = next((d for d in active if (d.name or "") == target_name), None)
        if not ds:
            ds = next((d for d in active if (d.name or "").lower() == target_name.lower()), None)
        if ds:
            return int(ds.id)

    # 2) 同类型且唯一（测试 doris → 生产唯一的 doris，名字可以不同）
    if ds_type:
        typed = [d for d in active if (d.ds_type or "").lower() == ds_type]
        if len(typed) == 1:
            return int(typed[0].id)
        if len(typed) > 1 and name:
            # 名字含关键字时再猜一次，例如 test_doris → prod_doris
            needle = re.sub(r"^(test|prod|dev|uat)_?", "", name.lower())
            fuzzy = [d for d in typed if needle and needle in (d.name or "").lower()]
            if len(fuzzy) == 1:
                return int(fuzzy[0].id)

    # 3) 空间默认 / 数仓数据源
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if ws:
        for cand in (getattr(ws, "warehouse_datasource_id", None), getattr(ws, "default_datasource_id", None)):
            if cand and any(d.id == cand for d in active):
                return int(cand)

    # 4) 空间里只有一个可用数据源
    if len(active) == 1:
        return int(active[0].id)

    if not name and not ds_type:
        return None

    hint = name or ds_type or "?"
    raise HTTPException(
        status_code=400,
        detail=(
            f"无法自动匹配数据源「{hint}」。请保证生产空间有同名数据源，"
            f"或同类型只保留一个，或设置空间默认数据源。"
        ),
    )


def _sanitize_params(raw_params: Any) -> List[dict]:
    if not isinstance(raw_params, list):
        return []
    out: List[dict] = []
    for i, p in enumerate(raw_params):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "param_in": str(p.get("param_in") or "query"),
                "data_type": str(p.get("data_type") or "string"),
                "required": bool(p.get("required")),
                "default_value": p.get("default_value"),
                "description": p.get("description"),
                "validator_regex": p.get("validator_regex"),
                "sort_order": int(p.get("sort_order") if p.get("sort_order") is not None else i),
            }
        )
    return out


def _apply_item_fields(api: DataApi, item: dict, datasource_id: Optional[int]) -> None:
    api.name = str(item.get("name") or api.api_code).strip()
    api.description = item.get("description")
    api.mode = str(item.get("mode") or "sql").strip().lower()
    api.http_method = str(item.get("http_method") or "GET").strip().upper()
    api.datasource_id = datasource_id
    api.sql_template = item.get("sql_template")
    api.wizard_config = item.get("wizard_config")
    api.response_fields = item.get("response_fields")
    api.pagination_enabled = bool(item.get("pagination_enabled", True))
    api.page_size_default = int(item.get("page_size_default") or 20)
    api.page_size_max = int(item.get("page_size_max") or 1000)
    api.timeout_seconds = int(item.get("timeout_seconds") or 30)
    api.cache_ttl_seconds = int(item.get("cache_ttl_seconds") or 0)
    api.max_rows = int(item.get("max_rows") or 10000)


def _sync_params_dicts(db: Session, api: DataApi, params: List[dict]) -> None:
    # 先清掉 identity map 中的旧 params，避免 delete+reinsert 同主键告警
    for old in list(api.params or []):
        db.delete(old)
    db.flush()
    for i, p in enumerate(params):
        db.add(
            DataApiParam(
                api_id=api.id,
                name=p["name"],
                param_in=p.get("param_in") or "query",
                data_type=p.get("data_type") or "string",
                required=bool(p.get("required")),
                default_value=p.get("default_value"),
                description=p.get("description"),
                validator_regex=p.get("validator_regex"),
                sort_order=int(p.get("sort_order") if p.get("sort_order") is not None else i),
            )
        )
    db.flush()


def _build_pending_definition(
    item: dict,
    *,
    datasource_id: Optional[int],
    params: List[dict],
    user_id: int,
) -> dict:
    """挂到已上线 API 旁的待发布快照（网关仍读线上字段）。"""
    defn = {
        "name": item.get("name"),
        "description": item.get("description"),
        "mode": str(item.get("mode") or "sql").strip().lower(),
        "http_method": str(item.get("http_method") or "GET").strip().upper(),
        "datasource_id": datasource_id,
        "sql_template": item.get("sql_template"),
        "wizard_config": item.get("wizard_config"),
        "response_fields": item.get("response_fields"),
        "pagination_enabled": bool(item.get("pagination_enabled", True)),
        "page_size_default": int(item.get("page_size_default") or 20),
        "page_size_max": int(item.get("page_size_max") or 1000),
        "timeout_seconds": int(item.get("timeout_seconds") or 30),
        "cache_ttl_seconds": int(item.get("cache_ttl_seconds") or 0),
        "max_rows": int(item.get("max_rows") or 10000),
        "params": params,
    }
    if defn["mode"] == "wizard" and defn.get("wizard_config"):
        defn["sql_template"] = wizard_to_sql(defn["wizard_config"] or {}, _param_ns(params))  # type: ignore[arg-type]
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "staged_at": _utcnow_iso(),
        "staged_by": user_id,
        "source_status": item.get("source_status"),
        "source_version": item.get("source_version"),
        "definition": defn,
    }


def apply_pending_definition(db: Session, api: DataApi) -> bool:
    """若存在 pending，写入线上字段并清空 pending；返回是否应用了 pending。"""
    pending = api.pending_definition
    if not isinstance(pending, dict):
        return False
    defn = pending.get("definition")
    if not isinstance(defn, dict):
        api.pending_definition = None
        return False
    params = _sanitize_params(defn.get("params"))
    _apply_item_fields(
        api,
        {
            **defn,
            # datasource_id 已在 definition 里；_apply 用第三个参数
        },
        defn.get("datasource_id"),
    )
    _sync_params_dicts(db, api, params)
    _maybe_compile_wizard_sql(api, params)
    api.pending_definition = None
    return True


def import_api_bundle(
    db: Session,
    *,
    workspace_id: int,
    bundle: dict,
    user_id: int,
    on_conflict: str = "overwrite",
    datasource_map: Optional[Dict[str, str]] = None,
) -> dict:
    """导入配置包。

    on_conflict:
      - skip: 已存在则跳过
      - overwrite:
          * 已上线：写入 pending_definition，线上不停服，待发布后切换
          * 草稿/下线：直接覆盖并保持/置为 draft
      - fail: 已存在则整包失败
    """
    policy = (on_conflict or "overwrite").strip().lower()
    if policy not in ("skip", "overwrite", "fail"):
        raise HTTPException(status_code=400, detail="on_conflict 仅支持 skip | overwrite | fail")
    ds_map = {str(k): str(v) for k, v in (datasource_map or {}).items()}

    _ver, items = _normalize_bundle(bundle)
    created, updated, skipped, errors = [], [], [], []

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append({"index": idx, "error": "api 项须为对象"})
            continue
        code = str(item.get("api_code") or "").strip().lower()
        if not _API_CODE_RE.match(code):
            errors.append({"index": idx, "api_code": code or None, "error": "api_code 非法"})
            continue
        item["_normalized_code"] = code

    if policy == "fail":
        for item in items:
            code = item.get("_normalized_code")
            if not code:
                continue
            exists = (
                db.query(DataApi.id)
                .filter(DataApi.workspace_id == workspace_id, DataApi.api_code == code)
                .first()
            )
            if exists:
                raise HTTPException(status_code=409, detail=f"api_code 已存在: {code}（on_conflict=fail）")

    if errors and not any(i.get("_normalized_code") for i in items if isinstance(i, dict)):
        raise HTTPException(status_code=400, detail={"message": "配置包无有效 API", "errors": errors})

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        code = item.get("_normalized_code")
        if not code:
            continue
        try:
            datasource_id = _resolve_datasource_id(db, workspace_id, item, ds_map)
            params = _sanitize_params(item.get("params"))
            existing = (
                db.query(DataApi)
                .options(joinedload(DataApi.params))
                .filter(DataApi.workspace_id == workspace_id, DataApi.api_code == code)
                .first()
            )
            if existing:
                if policy == "skip":
                    skipped.append({"api_code": code, "id": existing.id, "action": "skipped"})
                    continue
                if (existing.status or "").lower() == "online":
                    # 专业做法：线上继续服务，变更挂 pending，发布时原子切换
                    existing.pending_definition = _build_pending_definition(
                        item, datasource_id=datasource_id, params=params, user_id=user_id
                    )
                    existing.updated_at = datetime.utcnow()
                    db.flush()
                    updated.append(
                        {
                            "api_code": code,
                            "id": existing.id,
                            "action": "staged_pending",
                            "status": "online",
                            "has_pending_publish": True,
                        }
                    )
                else:
                    _apply_item_fields(existing, item, datasource_id)
                    _sync_params_dicts(db, existing, params)
                    _maybe_compile_wizard_sql(existing, params)
                    existing.status = "draft"
                    existing.pending_definition = None
                    existing.updated_at = datetime.utcnow()
                    db.flush()
                    updated.append(
                        {
                            "api_code": code,
                            "id": existing.id,
                            "action": "updated_draft",
                            "status": "draft",
                        }
                    )
            else:
                api = DataApi(
                    workspace_id=workspace_id,
                    api_code=code,
                    status="draft",
                    version=1,
                    owner_id=user_id,
                    created_by=user_id,
                )
                _apply_item_fields(api, item, datasource_id)
                db.add(api)
                db.flush()
                _sync_params_dicts(db, api, params)
                _maybe_compile_wizard_sql(api, params)
                db.flush()
                created.append({"api_code": code, "id": api.id, "action": "created_draft"})
        except HTTPException as e:
            detail = e.detail
            errors.append({"index": idx, "api_code": code, "error": detail if isinstance(detail, str) else detail})
        except Exception as e:
            errors.append({"index": idx, "api_code": code, "error": str(e)})

    if not created and not updated and errors:
        db.rollback()
        raise HTTPException(status_code=400, detail={"message": "导入失败", "errors": errors})

    db.commit()
    staged = sum(1 for u in updated if u.get("action") == "staged_pending")
    drafts = sum(1 for u in updated if u.get("action") == "updated_draft")
    return {
        "ok": True,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "on_conflict": policy,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "message": (
            f"导入完成：新建草稿 {len(created)}，已上线挂待发布 {staged}，覆盖为草稿 {drafts}，跳过 {len(skipped)}"
            + (f"，失败 {len(errors)}" if errors else "")
            + "。已上线接口不停服，发布后才会切换到新配置。"
        ),
    }
