# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""S3 认证：平台默认 + 各 Operator 集群 Profile 独立 IRSA / AK/SK。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.operator_runtime import OperatorRuntimeContext

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


def _strip_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _is_s3_uri(value: Optional[str]) -> bool:
    v = (value or "").strip().lower()
    return v.startswith("s3://") or v.startswith("s3a://")


def _first_non_empty_from_settings_or_env(names: Tuple[str, ...]) -> Optional[str]:
    for name in names:
        val = getattr(settings, name, None)
        if val is not None and str(val).strip():
            return str(val).strip()
        env_val = os.environ.get(name)
        if env_val is not None and str(env_val).strip():
            return str(env_val).strip()
    return None


@dataclass(frozen=True)
class S3AuthSnapshot:
    """单次提交/上传解析后的 S3 认证快照。"""

    auth_mode: Optional[str]
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    source: str = "platform"

    def static_credentials_ready(self) -> bool:
        return bool(self.access_key_id and self.secret_access_key)


def s3_paths_configured(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> bool:
    from app.services.artifact_s3 import artifact_s3_enabled

    if runtime_ctx and _is_s3_uri(runtime_ctx.checkpoint_dir):
        return True
    if artifact_s3_enabled():
        return True
    ckpt = (settings.FLINK_OPERATOR_CHECKPOINT_DIR or "").strip().lower()
    if ckpt.startswith("s3://") or ckpt.startswith("s3a://"):
        return True
    wh = (settings.PAIMON_WAREHOUSE_DEFAULT or "").strip().lower()
    return wh.startswith("s3://") or wh.startswith("s3a://")


def static_credentials_configured(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> bool:
    snap = build_s3_auth_snapshot(runtime_ctx)
    return snap.static_credentials_ready()


def resolved_static_credentials(
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    snap = build_s3_auth_snapshot(runtime_ctx)
    return snap.access_key_id, snap.secret_access_key, snap.session_token


def _platform_s3_auth_mode() -> Optional[str]:
    explicit = (getattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", None) or "").strip().lower()
    if explicit in (S3_AUTH_IRSA, S3_AUTH_STATIC):
        return explicit
    if static_credentials_configured() and not getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True):
        return S3_AUTH_STATIC
    if getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True):
        return S3_AUTH_IRSA
    if _first_non_empty_from_settings_or_env(_STATIC_KEY_FIELDS) and _first_non_empty_from_settings_or_env(
        _STATIC_SECRET_FIELDS
    ):
        return S3_AUTH_STATIC
    return S3_AUTH_IRSA


def _profile_explicit_mode(runtime_ctx: "OperatorRuntimeContext") -> Optional[str]:
    mode = _strip_or_none(getattr(runtime_ctx, "s3_auth_mode", None))
    if mode in (S3_AUTH_IRSA, S3_AUTH_STATIC):
        return mode
    return None


def build_s3_auth_snapshot(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> S3AuthSnapshot:
    region = _strip_or_none(getattr(settings, "GIDO_ARTIFACT_S3_REGION", None))
    endpoint = _strip_or_none(getattr(settings, "GIDO_ARTIFACT_S3_ENDPOINT_URL", None))

    if runtime_ctx is not None and runtime_ctx.profile_id is not None:
        ak = _strip_or_none(getattr(runtime_ctx, "s3_access_key_id", None))
        sk = _strip_or_none(getattr(runtime_ctx, "s3_secret_access_key", None))
        token = _strip_or_none(getattr(runtime_ctx, "s3_session_token", None))
        explicit_mode = _profile_explicit_mode(runtime_ctx)

        if ak and sk:
            return S3AuthSnapshot(
                auth_mode=S3_AUTH_STATIC,
                access_key_id=ak,
                secret_access_key=sk,
                session_token=token,
                region=region,
                endpoint_url=endpoint,
                source="profile",
            )
        if explicit_mode == S3_AUTH_STATIC:
            return S3AuthSnapshot(
                auth_mode=S3_AUTH_STATIC,
                access_key_id=ak,
                secret_access_key=sk,
                session_token=token,
                region=region,
                endpoint_url=endpoint,
                source="profile",
            )
        if explicit_mode == S3_AUTH_IRSA:
            return S3AuthSnapshot(
                auth_mode=S3_AUTH_IRSA,
                region=region,
                endpoint_url=endpoint,
                source="profile",
            )

    ak = _first_non_empty_from_settings_or_env(_STATIC_KEY_FIELDS)
    sk = _first_non_empty_from_settings_or_env(_STATIC_SECRET_FIELDS)
    token = _first_non_empty_from_settings_or_env(_STATIC_TOKEN_FIELDS)
    mode = _platform_s3_auth_mode()
    return S3AuthSnapshot(
        auth_mode=mode,
        access_key_id=ak,
        secret_access_key=sk,
        session_token=token,
        region=region,
        endpoint_url=endpoint,
        source="platform",
    )


def resolved_s3_auth_mode(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> Optional[str]:
    if not s3_paths_configured(runtime_ctx):
        return None
    return build_s3_auth_snapshot(runtime_ctx).auth_mode


def validate_s3_auth_for_submit(
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> Tuple[bool, str]:
    """提交前校验：static 模式须 AK/SK 齐全（优先 Profile，其次平台）。"""
    if not s3_paths_configured(runtime_ctx):
        return True, ""
    snap = build_s3_auth_snapshot(runtime_ctx)
    if snap.auth_mode != S3_AUTH_STATIC:
        return True, ""
    if snap.static_credentials_ready():
        return True, ""
    if snap.source == "profile":
        return (
            False,
            "该 Operator 集群 S3 认证为 static，但未配置 Access Key / Secret Key。"
            "请在「Operator 集群」编辑页填写 S3 AK/SK。",
        )
    return (
        False,
        "FLINK_OPERATOR_S3_AUTH_MODE=static 但未配置 GIDO_S3_ACCESS_KEY_ID / "
        "GIDO_S3_SECRET_ACCESS_KEY（或 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY）。",
    )


def apply_flink_s3_flink_conf(
    flink_conf: Dict[str, str],
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> None:
    """向 FlinkDeployment flinkConfiguration 注入 S3A 凭证 Provider 与 endpoint。"""
    if not s3_paths_configured(runtime_ctx):
        return
    snap = build_s3_auth_snapshot(runtime_ctx)
    if snap.auth_mode == S3_AUTH_IRSA:
        provider = (settings.FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER or IRSA_CREDENTIALS_PROVIDER).strip()
    elif snap.auth_mode == S3_AUTH_STATIC:
        provider = STATIC_CREDENTIALS_PROVIDER
    else:
        return

    if provider:
        flink_conf["fs.s3a.aws.credentials.provider"] = provider

    endpoint = snap.endpoint_url or _strip_or_none(getattr(settings, "GIDO_ARTIFACT_S3_ENDPOINT_URL", None))
    if endpoint:
        flink_conf["fs.s3a.endpoint"] = endpoint
        flink_conf["fs.s3a.path.style.access"] = "true"
        flink_conf["fs.s3a.connection.ssl.enabled"] = (
            "false" if endpoint.lower().startswith("http://") else "true"
        )


def flink_s3_credentials_env(
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> List[Dict[str, str]]:
    """Flink Pod 环境变量（static 模式注入 AWS_*）。"""
    snap = build_s3_auth_snapshot(runtime_ctx)
    if snap.auth_mode != S3_AUTH_STATIC or not snap.static_credentials_ready():
        return []
    env: List[Dict[str, str]] = [
        {"name": "AWS_ACCESS_KEY_ID", "value": snap.access_key_id or ""},
        {"name": "AWS_SECRET_ACCESS_KEY", "value": snap.secret_access_key or ""},
    ]
    if snap.session_token:
        env.append({"name": "AWS_SESSION_TOKEN", "value": snap.session_token})
    if snap.region:
        env.append({"name": "AWS_DEFAULT_REGION", "value": snap.region})
    return env


def operator_s3_credentials_pod_template(
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> Optional[Dict[str, Any]]:
    env = flink_s3_credentials_env(runtime_ctx)
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


def boto3_client_kwargs(
    runtime_ctx: Optional["OperatorRuntimeContext"] = None,
) -> Dict[str, Any]:
    """Backend 上传制品：Profile / 平台 static 显式传 AK/SK；irsa 走默认凭证链。"""
    snap = build_s3_auth_snapshot(runtime_ctx)
    kwargs: Dict[str, Any] = {}
    if snap.region:
        kwargs["region_name"] = snap.region
    if snap.endpoint_url:
        kwargs["endpoint_url"] = snap.endpoint_url
    if snap.auth_mode == S3_AUTH_STATIC and snap.static_credentials_ready():
        kwargs["aws_access_key_id"] = snap.access_key_id
        kwargs["aws_secret_access_key"] = snap.secret_access_key
        if snap.session_token:
            kwargs["aws_session_token"] = snap.session_token
    return kwargs


def s3_auth_public_summary(runtime_ctx: Optional["OperatorRuntimeContext"] = None) -> Dict[str, Any]:
    snap = build_s3_auth_snapshot(runtime_ctx)
    return {
        "s3_auth_mode": snap.auth_mode,
        "s3_auth_source": snap.source,
        "s3_paths_configured": s3_paths_configured(runtime_ctx),
        "static_credentials_configured": snap.static_credentials_ready(),
    }
