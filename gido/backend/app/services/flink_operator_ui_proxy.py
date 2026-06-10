# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""经 GIDO Backend 反向代理 Operator Flink Web UI（集群内 Backend 可达 JM REST，浏览器只访问 GIDO）。"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Dict, Iterable, Optional, Tuple

import requests
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import create_access_token
from app.services.flink_operator_submit import resolve_operator_jm_rest

logger = logging.getLogger(__name__)

_PROXY_COOKIE = "gido_flink_ui_proxy"
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
}


def operator_ui_proxy_enabled() -> bool:
    return bool(getattr(settings, "FLINK_OPERATOR_UI_PROXY_ENABLED", False))


def operator_ui_proxy_prefix(job_id: int) -> str:
    return f"/api/streaming/jobs/{int(job_id)}/flink-ui"


def operator_ui_proxy_browser_base(job_id: int) -> str:
    return operator_ui_proxy_prefix(job_id)


def _proxy_cookie_path(job_id: int) -> str:
    # 无尾斜杠，兼容 /flink-ui 与 /flink-ui/ 两种路径
    return operator_ui_proxy_prefix(job_id)


def issue_ui_proxy_cookie(job_id: int, user_id: int) -> Tuple[str, str]:
    """返回 (cookie_name, cookie_value)。"""
    token = create_access_token(
        {"sub": str(user_id), "scope": f"flink_ui_proxy:{int(job_id)}"},
        expires_delta=timedelta(hours=4),
    )
    return _PROXY_COOKIE, token


def validate_ui_proxy_cookie(job_id: int, cookie_value: Optional[str]) -> bool:
    if not cookie_value:
        return False
    try:
        from jose import jwt

        payload = jwt.decode(cookie_value, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        scope = (payload.get("scope") or "").strip()
        return scope == f"flink_ui_proxy:{int(job_id)}"
    except Exception:
        return False


def resolve_job_jm_rest(
    job_id: int,
    deployment_name: str,
    namespace: Optional[str] = None,
    stored: Optional[str] = None,
) -> str:
    ns = (namespace or settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
    jm = resolve_operator_jm_rest(deployment_name, ns, job_id=job_id, deadline_seconds=12.0)
    if not jm:
        jm = (stored or "").strip().rstrip("/")
    if not jm:
        raise HTTPException(status_code=503, detail="JM REST 尚未就绪，请稍后刷新作业状态后重试")
    return jm.rstrip("/")


def _rewrite_html(body: bytes, proxy_prefix: str) -> bytes:
    text = body.decode("utf-8", errors="replace")
    prefix = proxy_prefix.rstrip("/") + "/"
    base_tag = f'<base href="{prefix}">'
    # 先改绝对路径（勿在插入 base 之后做 blind replace，否则会把 base href 再拼一层前缀）
    esc = re.escape(prefix)
    text = re.sub(rf'href="/(?!{esc})', f'href="{prefix}', text)
    text = re.sub(rf"href='/(?!{esc})", f"href='{prefix}", text)
    text = re.sub(rf'src="/(?!{esc})', f'src="{prefix}', text)
    text = re.sub(rf"src='/(?!{esc})", f"src='{prefix}", text)
    if "<base " in text.lower():
        text = re.sub(r"(?is)<base\s[^>]*>", base_tag, text, count=1)
    elif "<head" in text.lower():
        text = re.sub(r"(?is)(<head[^>]*>)", r"\1" + base_tag, text, count=1)
    else:
        text = base_tag + text
    return text.encode("utf-8")


def _filter_response_headers(headers: Iterable[Tuple[str, str]], proxy_prefix: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in headers:
        lk = key.lower()
        if lk in _HOP_BY_HOP:
            continue
        if lk == "location" and value.startswith("/"):
            out[key] = proxy_prefix.rstrip("/") + value
            continue
        out[key] = value
    return out


def proxy_flink_ui_request(
    *,
    job_id: int,
    deployment_name: str,
    namespace: str,
    stored_jm_rest: Optional[str],
    subpath: str,
    method: str,
    query_string: str,
    incoming_headers: Dict[str, str],
) -> Tuple[int, Dict[str, str], bytes]:
    jm_base = resolve_job_jm_rest(job_id, deployment_name, namespace, stored_jm_rest)
    proxy_prefix = operator_ui_proxy_prefix(job_id)
    path = (subpath or "").lstrip("/")
    upstream = f"{jm_base}/{path}" if path else f"{jm_base}/"
    if query_string:
        upstream = f"{upstream}?{query_string}"

    fwd_headers = {
        k: v
        for k, v in incoming_headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
    }

    try:
        resp = requests.request(
            method=method.upper(),
            url=upstream,
            headers=fwd_headers,
            timeout=60,
            allow_redirects=False,
        )
    except requests.RequestException as ex:
        logger.warning("Flink UI 代理上游失败 job=%s url=%s: %s", job_id, upstream, ex)
        raise HTTPException(status_code=502, detail=f"无法连接 Flink JM UI：{ex}") from ex

    body = resp.content or b""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/html" in ctype and body:
        body = _rewrite_html(body, proxy_prefix)

    return resp.status_code, _filter_response_headers(resp.headers.items(), proxy_prefix), body
