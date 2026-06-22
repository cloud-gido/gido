# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""解析 JAR 作业 flink.nacos.* 参数并从 Nacos Open API 拉取配置正文（UI 预览）。"""
from __future__ import annotations

import logging
import os
import shlex
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_NACOS_PREFIX = "flink.nacos."
_REQUIRED = (
    "flink.nacos.dataId",
    "flink.nacos.group",
    "flink.nacos.serverAddr",
)


def _parse_allowed_hosts(raw: Optional[str]) -> Optional[frozenset[str]]:
    if not raw or not str(raw).strip():
        return None
    hosts = {h.strip().lower() for h in str(raw).replace(";", ",").split(",") if h.strip()}
    return frozenset(hosts) if hosts else None


def _host_allowed(hostname: str, allowed: Optional[frozenset[str]]) -> bool:
    host = (hostname or "").strip().lower()
    if not host:
        return False
    if allowed is None:
        return True
    if host in allowed:
        return True
    return any(host == pat or host.endswith(f".{pat}") for pat in allowed)


def validate_nacos_server_addr(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("flink.nacos.serverAddr 不能为空")
    parsed = urlparse(u)
    scheme = (parsed.scheme or "").lower()
    allowed = frozenset({"https", "http"}) if settings.GIDO_NACOS_ALLOW_INSECURE else frozenset({"https"})
    if scheme not in allowed:
        raise ValueError(f"flink.nacos.serverAddr 须为 HTTP(S) URL（当前 scheme: {scheme or '无'}）")
    if not parsed.netloc:
        raise ValueError("flink.nacos.serverAddr 缺少主机名")
    allowed_hosts = _parse_allowed_hosts(settings.GIDO_NACOS_ALLOWED_HOSTS)
    if not _host_allowed(parsed.hostname or "", allowed_hosts):
        raise ValueError(f"Nacos 主机不在白名单内: {parsed.hostname}")
    return u


def parse_nacos_params_from_program_args(program_args: Optional[str]) -> Dict[str, str]:
    """解析 --flink.nacos.key value 或 --flink.nacos.key=value。"""
    raw = (program_args or "").strip()
    if not raw:
        return {}
    out: Dict[str, str] = {}
    try:
        tokens = shlex.split(raw, posix=(os.name != "nt"))
    except ValueError:
        tokens = raw.split()

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--") and "=" in tok:
            key = tok[2:].split("=", 1)[0]
            val = tok.split("=", 1)[1]
            if key.startswith(_NACOS_PREFIX):
                out[key] = val
            i += 1
            continue
        if tok.startswith("--"):
            key = tok[2:]
            if key.startswith(_NACOS_PREFIX) and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                out[key] = tokens[i + 1]
                i += 2
                continue
        i += 1
    return out


def _merge_nacos_from_streaming_properties(props: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not props:
        return {}
    out: Dict[str, str] = {}
    for k, v in props.items():
        if v is None:
            continue
        ks = str(k)
        if ks.startswith(_NACOS_PREFIX):
            out[ks] = str(v).strip()
    fc = props.get("flinkConfiguration")
    if isinstance(fc, dict):
        for k, v in fc.items():
            if v is None:
                continue
            ks = str(k)
            if ks.startswith(_NACOS_PREFIX):
                out[ks] = str(v).strip()
    return out


def merge_nacos_params(
    program_args: Optional[str],
    streaming_properties: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """program_args 优先，streaming_properties 补缺。"""
    from_args = parse_nacos_params_from_program_args(program_args)
    from_props = _merge_nacos_from_streaming_properties(streaming_properties)
    merged = dict(from_props)
    merged.update(from_args)
    return merged


def nacos_ref_public(params: Dict[str, str]) -> Dict[str, Optional[str]]:
    return {
        "data_id": params.get("flink.nacos.dataId"),
        "group": params.get("flink.nacos.group"),
        "namespace_id": params.get("flink.nacos.namespaceId"),
        "server_addr": params.get("flink.nacos.serverAddr"),
        "username": params.get("flink.nacos.username"),
    }


def fetch_nacos_config_content(params: Dict[str, str]) -> str:
    missing = [k for k in _REQUIRED if not (params.get(k) or "").strip()]
    if missing:
        raise ValueError(f"缺少 Nacos 参数: {', '.join(missing)}")

    server = validate_nacos_server_addr(params["flink.nacos.serverAddr"])
    data_id = params["flink.nacos.dataId"].strip()
    group = params["flink.nacos.group"].strip()
    tenant = (params.get("flink.nacos.namespaceId") or "").strip()
    username = (params.get("flink.nacos.username") or "").strip()
    password = params.get("flink.nacos.password") or ""

    url = f"{server}/nacos/v1/cs/configs"
    query: Dict[str, str] = {"dataId": data_id, "group": group}
    if tenant:
        query["tenant"] = tenant

    auth = (username, password) if username else None
    timeout = float(getattr(settings, "GIDO_NACOS_DOWNLOAD_TIMEOUT_SECONDS", 15.0) or 15.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, params=query, auth=auth)
    except httpx.RequestError as ex:
        raise ValueError(f"无法连接 Nacos ({server}): {ex}") from ex

    if resp.status_code == 404:
        raise ValueError(f"Nacos 上未找到配置 dataId={data_id} group={group} tenant={tenant or '(default)'}")
    if resp.status_code in (401, 403):
        raise ValueError(f"Nacos 鉴权失败（HTTP {resp.status_code}），请检查 username/password")
    if resp.status_code >= 400:
        raise ValueError(f"Nacos 返回 HTTP {resp.status_code}: {(resp.text or '')[:200]}")

    content = resp.text or ""
    if not content.strip():
        raise ValueError("Nacos 配置内容为空")
    return content


def build_nacos_preview_payload(
    program_args: Optional[str],
    streaming_properties: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    params = merge_nacos_params(program_args, streaming_properties)
    ref = nacos_ref_public(params)
    if not any(ref.values()) and not params:
        return {
            "ref": ref,
            "params": {},
            "content": None,
            "error": "未解析到 flink.nacos.* 参数，请在运行参数中填写 --flink.nacos.dataId 等",
        }

    public_params = {k: v for k, v in params.items() if k != "flink.nacos.password"}
    try:
        content = fetch_nacos_config_content(params)
        return {"ref": ref, "params": public_params, "content": content, "error": None}
    except ValueError as ex:
        return {"ref": ref, "params": public_params, "content": None, "error": str(ex)}
    except Exception as ex:
        logger.warning("Nacos preview failed: %s", ex, exc_info=True)
        return {"ref": ref, "params": public_params, "content": None, "error": str(ex)}
