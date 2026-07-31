# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Redis-backed state shared by all backend replicas."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Optional
from urllib.parse import quote, urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None
_client_url = ""
_lock = threading.Lock()
_retry_after = 0.0

# rediss://host.example:AUTH_TOKEN 或 redis://host:TOKEN/db —— 密码被误写在端口位
_MISPLACED_TOKEN_RE = re.compile(
    r"^(?P<scheme>rediss?://)(?P<host>[^:/@]+):(?P<token>[^/@]+)(?P<path>/.*)?$",
    re.IGNORECASE,
)


def shared_state_required() -> bool:
    return bool(settings.SHARED_STATE_REQUIRED)


def key(*parts: object) -> str:
    prefix = (settings.SHARED_STATE_PREFIX or "gido").strip(":")
    return ":".join([prefix, *(str(part).strip(":") for part in parts)])


def normalize_redis_url(raw: str, password: str = "") -> str:
    """
    规范化 Redis 连接串（对齐 GISO/GiRisk）：

    - 完整 ``redis(s)://:token@host:6379/0`` 原样校验
    - 短主机名 / ``host:6379`` + ``REDIS_PASSWORD`` → 组装 URL
    - ``rediss://host:TOKEN``（token 误写在端口）→ ``rediss://:TOKEN@host:6379/0``
    """
    raw = (raw or "").strip()
    password = (password or "").strip()
    if not raw:
        return ""

    if "://" not in raw:
        host = raw
        port = 6379
        if ":" in raw:
            maybe_host, maybe_port = raw.rsplit(":", 1)
            if maybe_port.isdigit():
                host, port = maybe_host, int(maybe_port)
        auth = f":{quote(password, safe='')}@" if password else ""
        return f"redis://{auth}{host}:{port}/0"

    try:
        parsed = urlparse(raw)
        _ = parsed.port  # 触发非法端口校验
        if password and not parsed.password:
            user = quote(parsed.username or "", safe="")
            token = quote(password, safe="")
            host = parsed.hostname or ""
            port = parsed.port or 6379
            path = parsed.path or "/0"
            userinfo = f"{user}:{token}@" if user else f":{token}@"
            return f"{parsed.scheme}://{userinfo}{host}:{port}{path}"
        return raw
    except ValueError as ex:
        match = _MISPLACED_TOKEN_RE.match(raw)
        if match:
            scheme = match.group("scheme")
            host = match.group("host")
            token = quote(match.group("token"), safe="")
            path = match.group("path") or "/0"
            fixed = f"{scheme}:{token}@{host}:6379{path}"
            logger.warning(
                "REDIS_URL 疑似把密码写在端口位置，已改写为 redis(s)://:token@host:6379/…"
            )
            return fixed
        raise RuntimeError(
            "REDIS_URL 无法解析。请使用完整 URL：rediss://:URL编码密码@host:6379/0 "
            "（常见错误：把 auth token 写在 host:TOKEN 端口位）。"
            f" 底层错误: {ex}"
        ) from ex


def resolved_redis_url() -> str:
    return normalize_redis_url(
        settings.REDIS_URL or "",
        getattr(settings, "REDIS_PASSWORD", "") or "",
    )


def redis_client(*, required: Optional[bool] = None):
    """Return a verified Redis client, or None for local-development fallback."""
    global _client, _client_url, _retry_after
    must_exist = settings.SHARED_STATE_REQUIRED if required is None else required
    try:
        url = resolved_redis_url()
    except RuntimeError:
        if must_exist:
            raise
        return None
    if not url:
        if must_exist:
            raise RuntimeError("多副本共享状态已启用，但 REDIS_URL 未配置")
        return None

    now = time.monotonic()
    with _lock:
        if _client is not None and _client_url == url:
            return _client
        if now < _retry_after and not must_exist:
            return None
        try:
            import redis

            candidate = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=3,
                health_check_interval=30,
            )
            candidate.ping()
            _client = candidate
            _client_url = url
            _retry_after = 0.0
            return candidate
        except Exception as ex:
            _client = None
            _client_url = ""
            _retry_after = now + 5
            if must_exist:
                raise RuntimeError(f"Redis 共享状态不可用: {ex}") from ex
            logger.debug("Redis 不可用，使用单进程开发回退: %s", ex)
            return None


def reset_redis_client_for_tests() -> None:
    """Test helper: drop cached Redis client."""
    global _client, _client_url, _retry_after
    with _lock:
        _client = None
        _client_url = ""
        _retry_after = 0.0


def redis_ready() -> bool:
    try:
        client = redis_client(required=settings.SHARED_STATE_REQUIRED)
        return client is not None or not settings.SHARED_STATE_REQUIRED
    except Exception:
        return False


def _require_client_or_none():
    client = redis_client()
    if client is not None:
        return client
    if shared_state_required():
        raise RuntimeError("多副本共享状态已启用，禁止降级为进程内状态")
    return None


def rate_limit_hit(bucket: str, limit: int, window_sec: int) -> Optional[bool]:
    """Return whether the shared bucket exceeded its limit; None means local fallback."""
    client = _require_client_or_none()
    if client is None:
        return None
    lua = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    return current
    """
    current = int(client.eval(lua, 1, key("rate", bucket), int(window_sec)))
    return current > int(limit)


def claim_once(claim_key: str, ttl: int) -> Optional[bool]:
    """Atomically claim an idempotency key; None means local-development fallback."""
    client = _require_client_or_none()
    if client is None:
        return None
    return bool(client.set(key("claim", claim_key), "1", nx=True, ex=max(1, int(ttl))))


def cache_get(cache_key: str) -> Optional[Any]:
    client = redis_client(required=False)
    if client is None:
        return None
    raw = client.get(key("cache", cache_key))
    return json.loads(raw) if raw else None


def cache_set(cache_key: str, value: Any, ttl: int) -> bool:
    client = redis_client(required=False)
    if client is None:
        return False
    client.setex(key("cache", cache_key), max(1, int(ttl)), json.dumps(value, ensure_ascii=False))
    return True
