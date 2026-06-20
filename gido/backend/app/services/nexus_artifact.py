# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""从 Nexus（Maven 仓库）HTTP 下载 JAR 制品。

默认模式（推荐）：作业存 **内网临时开放** 的 HTTPS 直链，GIDO Backend 匿名 GET 即可；
链接本身可含 query token（由 Nexus/CI 签发），**无需** 平台配置账号密码。
仅当仓库仍要求 Basic Auth 时，可选配置 GIDO_NEXUS_USERNAME / PASSWORD。
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_JAR_MAGIC = b"PK\x03\x04"
_DEFAULT_ALLOWED_SCHEMES = frozenset({"https"})


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


def validate_nexus_jar_url(url: str) -> str:
    """校验 Nexus JAR URL；返回规范化字符串。"""
    u = (url or "").strip()
    if not u:
        raise ValueError("jar_nexus_url 不能为空")
    parsed = urlparse(u)
    scheme = (parsed.scheme or "").lower()
    allowed_schemes = _DEFAULT_ALLOWED_SCHEMES
    if settings.GIDO_NEXUS_ALLOW_INSECURE:
        allowed_schemes = frozenset({"https", "http"})
    if scheme not in allowed_schemes:
        raise ValueError(f"jar_nexus_url 须为 HTTPS 链接（当前 scheme: {scheme or '无'}）")
    if not parsed.netloc:
        raise ValueError("jar_nexus_url 缺少主机名")
    allowed_hosts = _parse_allowed_hosts(settings.GIDO_NEXUS_ALLOWED_HOSTS)
    if not _host_allowed(parsed.hostname or "", allowed_hosts):
        raise ValueError(f"jar_nexus_url 主机不在白名单内: {parsed.hostname}")
    path = (parsed.path or "").lower()
    if not path.endswith(".jar"):
        raise ValueError("jar_nexus_url 须指向 .jar 文件")
    return u


def _nexus_auth() -> Optional[tuple[str, str]]:
    """可选 Basic Auth；内网临时开放直链场景留空即可。"""
    user = (settings.GIDO_NEXUS_USERNAME or "").strip()
    password = settings.GIDO_NEXUS_PASSWORD or ""
    if user:
        return user, password
    return None


def _nexus_http_error_message(status_code: int, url: str) -> str:
    if status_code in (401, 403):
        return (
            f"Nexus 拒绝访问（HTTP {status_code}）。"
            "内网临时直链可能已过期、未开放或 Backend 不在可访问网段；"
            "请向 CI/Nexus 重新获取 jar_nexus_url。"
            f" URL: {url}"
        )
    if status_code == 404:
        return f"Nexus JAR 不存在（HTTP 404）: {url}"
    return f"Nexus 下载失败（HTTP {status_code}）: {url}"


def fetch_jar_bytes_from_nexus(url: str, *, timeout_seconds: Optional[float] = None) -> bytes:
    """从 Nexus 下载 JAR（默认匿名 GET；URL 可含临时 token query）。"""
    normalized = validate_nexus_jar_url(url)
    max_bytes = int(settings.GIDO_NEXUS_MAX_JAR_BYTES or 0) or 524_288_000
    timeout = float(timeout_seconds or settings.GIDO_NEXUS_DOWNLOAD_TIMEOUT_SECONDS or 120.0)
    auth = _nexus_auth()
    headers = {"Accept": "application/java-archive, application/octet-stream, */*"}
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            with client.stream("GET", normalized, auth=auth, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise RuntimeError(_nexus_http_error_message(resp.status_code, normalized))
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError(
                            f"Nexus JAR 超过大小上限（{max_bytes} 字节）: {normalized}"
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
    except httpx.TimeoutException as ex:
        raise RuntimeError(f"连接 Nexus 超时: {normalized}") from ex
    except httpx.HTTPError as ex:
        raise RuntimeError(f"连接 Nexus 失败: {ex}") from ex

    if len(content) < 4:
        raise RuntimeError("Nexus 响应为空或过小，不是有效 JAR")
    if content[:4] != _JAR_MAGIC:
        raise RuntimeError("Nexus 响应不是有效 JAR（缺少 ZIP 魔数 PK\\x03\\x04）")
    logger.info("已从 Nexus 下载 JAR url=%s bytes=%s", normalized, len(content))
    return content
