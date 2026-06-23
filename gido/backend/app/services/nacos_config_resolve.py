# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""从 Nacos 作业配置 + common 环境配置解析连接地址（不含密码类字段）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import yaml

_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|credential|apikey|api_key|private_key|access_key)",
    re.IGNORECASE,
)
_CONNECTION_KEY_RE = re.compile(
    r"(\.servers$|^servers$|bootstrap\.servers|\.bootstrap\.servers|"
    r"jdbc\.url|\.jdbc\.url|\.url$|\.host$|\.endpoint$|\.address$|"
    r"nameserver|name-server|broker-list|bootstrap)",
    re.IGNORECASE,
)


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(key or ""))


def _connection_kind(key: str, value: str) -> str:
    lk = (key or "").lower()
    lv = (value or "").lower()
    if "bootstrap" in lk or lk.endswith(".servers") or lk == "servers":
        return "kafka"
    if "jdbc" in lk or lv.startswith("jdbc:"):
        return "jdbc"
    if "redis" in lk:
        return "redis"
    if "rocketmq" in lk or "nameserver" in lk:
        return "rocketmq"
    if "mysql" in lk or "postgres" in lk or "mongodb" in lk:
        return "database"
    return "other"


def _looks_encrypted(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if text.startswith("cipher(") or text.startswith("ENC("):
        return True
    try:
        yaml.safe_load(text)
        return False
    except yaml.YAMLError:
        pass
    if "=" not in text and ":" not in text and len(text) > 80:
        return True
    return False


def flatten_mapping(data: Any, prefix: str = "") -> Dict[str, str]:
    """将嵌套 dict/list 扁平化为 dot-key → str value。"""
    out: Dict[str, str] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.update(flatten_mapping(v, key))
            elif v is not None and not isinstance(v, (dict, list)):
                out[key] = str(v).strip()
    elif isinstance(data, list) and prefix:
        out[prefix] = ",".join(str(x) for x in data)
    return out


def parse_config_to_flat_map(content: str, data_id: str = "") -> Dict[str, str]:
    text = (content or "").strip()
    if not text:
        return {}
    name = (data_id or "").lower()
    if name.endswith((".yml", ".yaml")) or text.lstrip().startswith(("{", "-")):
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return flatten_mapping(data)
        if data is None:
            return {}
        raise ValueError("YAML 根节点须为对象")
    # properties / 类 properties
    out: Dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        if ":" in s and not s.startswith("-") and "=" not in s.split(":")[0]:
            key, _, val = s.partition(":")
            out[key.strip()] = val.strip()
            continue
        if "=" in s:
            key, _, val = s.partition("=")
            out[key.strip()] = val.strip()
    return out


def resolve_placeholders(value: str, env_map: Dict[str, str]) -> Tuple[str, List[str]]:
    """替换 ${key}；返回 (结果, 未找到的 key 列表)。"""
    missing: List[str] = []

    def repl(m: re.Match[str]) -> str:
        ref = m.group(1).strip()
        if ref in env_map:
            return env_map[ref]
        missing.append(ref)
        return m.group(0)

    return _PLACEHOLDER_RE.sub(repl, value or ""), missing


def extract_connections_from_job(
    job_flat: Dict[str, str],
    env_map: Dict[str, str],
) -> Tuple[List[Dict[str, str]], List[str]]:
    """从作业配置中提取含占位符或可识别的连接项。"""
    connections: List[Dict[str, str]] = []
    unresolved: List[str] = []
    seen: set[str] = set()

    for key, raw in job_flat.items():
        if _is_sensitive_key(key):
            continue
        if not raw or "${" not in raw:
            continue
        if not _CONNECTION_KEY_RE.search(key):
            continue
        resolved, missing = resolve_placeholders(raw, env_map)
        unresolved.extend(m for m in missing if m not in unresolved)
        if resolved == raw:
            continue
        if _is_sensitive_key(resolved):
            continue
        dedupe = f"{key}|{resolved}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        ref_match = _PLACEHOLDER_RE.search(raw)
        connections.append({
            "config_key": key,
            "ref_key": ref_match.group(1) if ref_match else "",
            "value": resolved,
            "kind": _connection_kind(key, resolved),
        })

    return connections, unresolved


def extract_connections_from_env(env_map: Dict[str, str]) -> List[Dict[str, str]]:
    """从 common/env 扁平 map 直接提取连接类 env.* 项（供作业未显式引用时展示）。"""
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for key, value in env_map.items():
        if _is_sensitive_key(key) or not value:
            continue
        if not _CONNECTION_KEY_RE.search(key):
            continue
        dedupe = f"{key}|{value}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        out.append({
            "config_key": key,
            "ref_key": "",
            "value": value,
            "kind": _connection_kind(key, value),
        })
    return out


def build_connection_preview(
    job_content: str,
    job_data_id: str,
    common_content: Optional[str],
    common_data_id: str,
) -> Dict[str, Any]:
    """解析双份配置，返回连接信息（不含密码）。"""
    warnings: List[str] = []
    if common_content is None:
        warnings.append(f"未拉取 common 配置 {common_data_id}，无法解析 ${'{env.xxx}'}")
        return {"connections": [], "unresolved": [], "warnings": warnings, "common_loaded": False}

    if _looks_encrypted(common_content):
        warnings.append(
            f"common 配置 {common_data_id} 可能为加密内容，无法在平台侧解析连接地址"
        )
        return {"connections": [], "unresolved": [], "warnings": warnings, "common_loaded": True}

    try:
        env_map = parse_config_to_flat_map(common_content, common_data_id)
    except Exception as ex:
        warnings.append(f"common 配置解析失败: {ex}")
        return {"connections": [], "unresolved": [], "warnings": warnings, "common_loaded": True}

    # 只保留非敏感 env 条目供替换
    safe_env = {k: v for k, v in env_map.items() if not _is_sensitive_key(k)}

    try:
        job_flat = parse_config_to_flat_map(job_content, job_data_id)
    except Exception as ex:
        warnings.append(f"作业配置解析失败: {ex}")
        job_flat = {}

    from_job, unresolved = extract_connections_from_job(job_flat, safe_env)
    if from_job:
        return {
            "connections": from_job,
            "unresolved": unresolved,
            "warnings": warnings,
            "common_loaded": True,
            "common_data_id": common_data_id,
        }

    # 作业配置无显式引用时，从 common 的 env.* 连接项兜底展示
    from_env = extract_connections_from_env(safe_env)
    return {
        "connections": from_env,
        "unresolved": unresolved,
        "warnings": warnings if from_env else warnings + ["未在配置中识别到连接地址字段"],
        "common_loaded": True,
        "common_data_id": common_data_id,
    }
