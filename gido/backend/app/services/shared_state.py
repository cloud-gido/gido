# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Redis-backed state shared by all backend replicas."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import unquote, quote

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None
_client_url = ""
_lock = threading.Lock()
_retry_after = 0.0

# rediss://host:TOKEN —— token 误写在端口位（无 @）
_MISPLACED_TOKEN_RE = re.compile(
    r"^(?P<scheme>rediss?://)(?P<host>[^:/@]+):(?P<token>[^/@]+)(?P<path>/.*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RedisEndpoint:
    """对齐 GiRisk RedisUrlParser.Info / GISO RedisConnections。"""

    scheme: str
    host: str
    port: int
    username: str
    password: str
    database: int

    @property
    def ssl(self) -> bool:
        return self.scheme.lower() == "rediss"

    @property
    def cache_key(self) -> str:
        user = self.username or ""
        auth = f"{user}:***" if (user or self.password) else ""
        if auth:
            auth = f"{auth}@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}/{self.database}"


def shared_state_required() -> bool:
    return bool(settings.SHARED_STATE_REQUIRED)


def key(*parts: object) -> str:
    prefix = (settings.SHARED_STATE_PREFIX or "gido").strip(":")
    return ":".join([prefix, *(str(part).strip(":") for part in parts)])


def _percent_decode(value: str) -> str:
    if not value or "%" not in value:
        return value
    try:
        return unquote(value)
    except Exception:
        return value


def parse_redis_endpoint(raw: str, override_password: str = "") -> RedisEndpoint:
    """
    解析 Redis 连接（对齐 GiRisk RedisUrlParser，不依赖 urllib 解析密码）。

    平台 Doppler 常见：
    ``rediss://:sTtN?Yo5q...=!@master.xxx.cache.amazonaws.com/0``
    密码含 ``?`` ``=`` ``!`` 时，Python urllib / redis.from_url 会把 ``?`` 当成 query 截断。
    """
    raw = (raw or "").strip()
    override_password = (override_password or "").strip()
    if not raw:
        raise ValueError("empty redis url")

    # 短主机名 / host:6379 + 独立密码（GiRisk fromParts）
    if not raw.lower().startswith(("redis://", "rediss://")):
        host = raw
        port = 6379
        if ":" in raw:
            maybe_host, maybe_port = raw.rsplit(":", 1)
            if maybe_port.isdigit():
                host, port = maybe_host, int(maybe_port)
        scheme = "rediss" if ".amazonaws.com" in host else "redis"
        return RedisEndpoint(scheme, host, port, "", override_password, 0)

    # token 误写在端口：rediss://host:TOKEN
    misplaced = _MISPLACED_TOKEN_RE.match(raw)
    if misplaced and "@" not in raw:
        host = misplaced.group("host")
        token = _percent_decode(misplaced.group("token"))
        path = misplaced.group("path") or "/0"
        db = 0
        if path.strip("/").isdigit():
            db = int(path.strip("/"))
        scheme = "rediss" if misplaced.group("scheme").lower().startswith("rediss") else "redis"
        if ".amazonaws.com" in host:
            scheme = "rediss"
            db = 0
        return RedisEndpoint(scheme, host, 6379, "", token or override_password, db)

    # 与 GiRisk 相同：用最后一个 @ 分割 auth / host（密码可含 ? = ! :）
    at = raw.rfind("@")
    if at < 0:
        scheme = "rediss" if raw.lower().startswith("rediss://") else "redis"
        tail = raw.split("://", 1)[1]
        auth = ""
    else:
        head = raw[:at]
        tail = raw[at + 1 :]
        scheme = "rediss" if head.lower().startswith("rediss://") else "redis"
        auth = head.split("://", 1)[1]

    username = ""
    password = ""
    has_embedded = False
    if auth:
        if auth.startswith(":"):
            password = auth[1:]
            has_embedded = bool(password.strip())
        elif ":" in auth:
            username, password = auth.split(":", 1)
            has_embedded = bool(password.strip())
        else:
            password = auth
            has_embedded = bool(password.strip())
    password = _percent_decode(password.strip())
    username = _percent_decode(username.strip())
    if not has_embedded and override_password:
        password = override_password

    database = 0
    if "/" in tail:
        host_port, db_part = tail.split("/", 1)
        if db_part.strip().isdigit():
            database = int(db_part.strip())
    else:
        host_port = tail

    port = 6379
    if ":" in host_port:
        host, port_s = host_port.rsplit(":", 1)
        if not port_s.isdigit():
            raise ValueError(
                f"Redis 端口非法: {port_s!r}（若密码写在 host:TOKEN 位置，请改为 "
                "rediss://:TOKEN@host:6379/0）"
            )
        port = int(port_s)
    else:
        host = host_port

    host = (host or "").strip()
    if not host:
        raise ValueError("Redis host is empty")

    # ElastiCache：强制 TLS + db0（对齐 GiRisk）
    if ".amazonaws.com" in host:
        return RedisEndpoint("rediss", host, port, "", password, 0)
    return RedisEndpoint(scheme, host, port, username, password, database)


def resolved_redis_endpoint() -> Optional[RedisEndpoint]:
    raw = (settings.REDIS_URL or "").strip()
    password = (getattr(settings, "REDIS_PASSWORD", None) or "").strip()
    if not raw:
        return None
    return parse_redis_endpoint(raw, password)


def normalize_redis_url(raw: str, password: str = "") -> str:
    """测试/兼容：解析后再拼回可展示的 URL（密码会做 quote）。"""
    if not (raw or "").strip():
        return ""
    ep = parse_redis_endpoint(raw, password)
    user = quote(ep.username, safe="") if ep.username else ""
    token = quote(ep.password, safe="") if ep.password else ""
    if ep.password or ep.username:
        userinfo = f"{user}:{token}@" if ep.username else f":{token}@"
    else:
        userinfo = ""
    return f"{ep.scheme}://{userinfo}{ep.host}:{ep.port}/{ep.database}"


def redis_client(*, required: Optional[bool] = None):
    """Return a verified Redis client, or None for local-development fallback."""
    global _client, _client_url, _retry_after
    must_exist = settings.SHARED_STATE_REQUIRED if required is None else required
    try:
        endpoint = resolved_redis_endpoint()
    except Exception as ex:
        if must_exist:
            raise RuntimeError(f"REDIS_URL 无法解析: {ex}") from ex
        logger.debug("REDIS_URL 无法解析，使用单进程开发回退: %s", ex)
        return None
    if endpoint is None:
        if must_exist:
            raise RuntimeError("多副本共享状态已启用，但 REDIS_URL 未配置")
        return None

    cache_key = endpoint.cache_key
    now = time.monotonic()
    with _lock:
        if _client is not None and _client_url == cache_key:
            return _client
        if now < _retry_after and not must_exist:
            return None
        try:
            import redis

            kwargs: dict[str, Any] = {
                "host": endpoint.host,
                "port": endpoint.port,
                "db": endpoint.database,
                "decode_responses": True,
                "socket_connect_timeout": 2,
                "socket_timeout": 3,
                "health_check_interval": 30,
            }
            if endpoint.password:
                kwargs["password"] = endpoint.password
            if endpoint.username:
                kwargs["username"] = endpoint.username
            if endpoint.ssl:
                kwargs["ssl"] = True
                kwargs["ssl_cert_reqs"] = None
            candidate = redis.Redis(**kwargs)
            candidate.ping()
            _client = candidate
            _client_url = cache_key
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
