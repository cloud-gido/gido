# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""S3 认证：平台级 IRSA 与静态 AK/SK（Flink Hadoop S3A + Backend boto3）。"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

S3_AUTH_IRSA = "irsa"
S3_AUTH_STATIC = "static"

IRSA_CREDENTIALS_PROVIDER = "com.amazonaws.auth.WebIdentityTokenCredentialsProvider"
STATIC_CREDENTIALS_PROVIDER = "com.amazonaws.auth.EnvironmentVariableCredentialsProvider"

_STATIC_KEY_FIELDS = (
    "GIDO_S3_ACCESS_KEY_ID",
    "AWS_ACCESS_KEY_ID",
)
_STATIC_SECRET_FIELDS = (
    "GIDO_S3_SECRET_ACCESS_KEY",
    "AWS_SECRET_ACCESS_KEY",
)
_STATIC_TOKEN_FIELDS = (
    "GIDO_S3_SESSION_TOKEN",
    "AWS_SESSION_TOKEN",
)


def _first_non_empty_from_settings_or_env(names: Tuple[str, ...]) -> Optional[str]:
    for name in names:
        val = getattr(settings, name, None)
        if val is not None and str(val).strip():
            return str(val).strip()
        env_val = os.environ.get(name)
        if env_val is not None and str(env_val).strip():
            return str(env_val).strip()
    return None


def s3_paths_configured() -> bool:
    from app.services.artifact_s3 import artifact_s3_enabled

    if artifact_s3_enabled():
        return True
    ckpt = (settings.FLINK_OPERATOR_CHECKPOINT_DIR or "").strip().lower()
    if ckpt.startswith("s3://") or ckpt.startswith("s3a://"):
        return True
    wh = (settings.PAIMON_WAREHOUSE_DEFAULT or "").strip().lower()
    return wh.startswith("s3://") or wh.startswith("s3a://")


def static_credentials_configured() -> bool:
    return bool(
        _first_non_empty_from_settings_or_env(_STATIC_KEY_FIELDS)
        and _first_non_empty_from_settings_or_env(_STATIC_SECRET_FIELDS)
    )


def resolved_static_credentials() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(access_key_id, secret_access_key, session_token)"""
    return (
        _first_non_empty_from_settings_or_env(_STATIC_KEY_FIELDS),
        _first_non_empty_from_settings_or_env(_STATIC_SECRET_FIELDS),
        _first_non_empty_from_settings_or_env(_STATIC_TOKEN_FIELDS),
    )


def resolved_s3_auth_mode() -> Optional[str]:
    """
    返回 irsa / static；未配置 S3 路径时返回 None。
    FLINK_OPERATOR_S3_AUTH_MODE=irsa|static；未设时兼容 FLINK_OPERATOR_S3_USE_IRSA。
    """
    if not s3_paths_configured():
        return None

    explicit = (getattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", None) or "").strip().lower()
    if explicit in (S3_AUTH_IRSA, S3_AUTH_STATIC):
        return explicit

    if static_credentials_configured() and not getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True):
        return S3_AUTH_STATIC

    if getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True):
        return S3_AUTH_IRSA

    if static_credentials_configured():
        return S3_AUTH_STATIC

    return S3_AUTH_IRSA


def validate_s3_auth_for_submit() -> Tuple[bool, str]:
    """提交前校验：static 模式须 AK/SK 齐全。"""
    mode = resolved_s3_auth_mode()
    if mode != S3_AUTH_STATIC:
        return True, ""
    ak, sk, _ = resolved_static_credentials()
    if ak and sk:
        return True, ""
    return (
        False,
        "FLINK_OPERATOR_S3_AUTH_MODE=static 但未配置 GIDO_S3_ACCESS_KEY_ID / "
        "GIDO_S3_SECRET_ACCESS_KEY（或 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY）。",
    )


def apply_flink_s3_flink_conf(flink_conf: Dict[str, str]) -> None:
    """向 FlinkDeployment flinkConfiguration 注入 S3A 凭证 Provider 与 endpoint。"""
    mode = resolved_s3_auth_mode()
    if not mode:
        return

    if mode == S3_AUTH_IRSA:
        provider = (settings.FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER or IRSA_CREDENTIALS_PROVIDER).strip()
    else:
        provider = STATIC_CREDENTIALS_PROVIDER

    if provider:
        flink_conf["fs.s3a.aws.credentials.provider"] = provider

    _apply_flink_s3_endpoint_conf(flink_conf)


def _apply_flink_s3_endpoint_conf(flink_conf: Dict[str, str]) -> None:
    endpoint = (getattr(settings, "GIDO_ARTIFACT_S3_ENDPOINT_URL", None) or "").strip()
    if not endpoint:
        return
    flink_conf["fs.s3a.endpoint"] = endpoint
    flink_conf["fs.s3a.path.style.access"] = "true"
    flink_conf["fs.s3a.connection.ssl.enabled"] = "false" if endpoint.lower().startswith("http://") else "true"


def flink_s3_credentials_env() -> List[Dict[str, str]]:
    """Flink Pod 环境变量（static 模式注入 AWS_*，供 EnvironmentVariableCredentialsProvider）。"""
    if resolved_s3_auth_mode() != S3_AUTH_STATIC:
        return []
    ak, sk, token = resolved_static_credentials()
    if not ak or not sk:
        return []
    env: List[Dict[str, str]] = [
        {"name": "AWS_ACCESS_KEY_ID", "value": ak},
        {"name": "AWS_SECRET_ACCESS_KEY", "value": sk},
    ]
    if token:
        env.append({"name": "AWS_SESSION_TOKEN", "value": token})
    region = (getattr(settings, "GIDO_ARTIFACT_S3_REGION", None) or "").strip()
    if region:
        env.append({"name": "AWS_DEFAULT_REGION", "value": region})
    return env


def operator_s3_credentials_pod_template() -> Optional[Dict[str, Any]]:
    env = flink_s3_credentials_env()
    if not env:
        return None
    return {
        "spec": {
            "containers": [
                {
                    "name": "flink-main-container",
                    "env": env,
                }
            ],
        }
    }


def boto3_client_kwargs() -> Dict[str, Any]:
    """Backend 上传制品：static 显式传 AK/SK；irsa 或未指定时走默认凭证链。"""
    kwargs: Dict[str, Any] = {}
    region = (getattr(settings, "GIDO_ARTIFACT_S3_REGION", None) or "").strip()
    if region:
        kwargs["region_name"] = region
    endpoint = (getattr(settings, "GIDO_ARTIFACT_S3_ENDPOINT_URL", None) or "").strip()
    if endpoint:
        kwargs["endpoint_url"] = endpoint

    mode = resolved_s3_auth_mode()
    if mode == S3_AUTH_STATIC:
        ak, sk, token = resolved_static_credentials()
        if ak and sk:
            kwargs["aws_access_key_id"] = ak
            kwargs["aws_secret_access_key"] = sk
            if token:
                kwargs["aws_session_token"] = token
    return kwargs


def s3_auth_public_summary() -> Dict[str, Any]:
    mode = resolved_s3_auth_mode()
    return {
        "s3_auth_mode": mode,
        "s3_paths_configured": s3_paths_configured(),
        "static_credentials_configured": static_credentials_configured(),
    }
