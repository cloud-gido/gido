# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Flink Operator 多集群运行时解析：Profile + 作业级镜像覆盖。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.flink_version import infer_operator_flink_version_from_image


def _strip_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        s = _strip_or_none(v)
        if s:
            return s
    return None


@dataclass(frozen=True)
class OperatorRuntimeContext:
    """单次 Operator 提交/运维所用的集群与镜像上下文。"""

    profile_id: Optional[int]
    profile_name: Optional[str]
    namespace: str
    image: str
    flink_version: str
    service_account: str
    k8s_context: Optional[str]
    kubeconfig_path: Optional[str]
    jm_rest_template: str
    cluster_domain: str
    checkpoint_dir: Optional[str]
    image_pull_secrets: Optional[str]
    s3_auth_mode: Optional[str]
    s3_access_key_id: Optional[str]
    s3_secret_access_key: Optional[str]
    s3_session_token: Optional[str]
    s3_region: Optional[str]
    s3_endpoint_url: Optional[str]
    jar_s3_prefix: Optional[str]

    @classmethod
    def from_settings(cls) -> "OperatorRuntimeContext":
        ns = (
            settings.FLINK_OPERATOR_NAMESPACE
            or settings.FLINK_K8S_NAMESPACE
            or "flink"
        ).strip() or "flink"
        image = (
            settings.FLINK_OPERATOR_IMAGE
            or settings.FLINK_K8S_APPLICATION_IMAGE
            or "apache/flink:2.2.1-java11"
        ).strip()
        jm_tpl = (
            settings.FLINK_OPERATOR_JM_REST_TEMPLATE
            or "http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081"
        ).strip()
        domain = (settings.FLINK_K8S_CLUSTER_DOMAIN or "cluster.local").strip() or "cluster.local"
        jar_prefix = _first_non_empty(
            getattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", None),
            getattr(settings, "GIDO_ARTIFACT_S3_PREFIX", None),
        )
        return cls(
            profile_id=None,
            profile_name=None,
            namespace=ns,
            image=image,
            flink_version=(settings.FLINK_OPERATOR_FLINK_VERSION or "v2_2").strip(),
            service_account=(settings.FLINK_OPERATOR_SERVICE_ACCOUNT or "flink").strip() or "flink",
            k8s_context=_strip_or_none(settings.FLINK_K8S_CONTEXT),
            kubeconfig_path=_strip_or_none(settings.FLINK_K8S_KUBECONFIG_PATH),
            jm_rest_template=jm_tpl,
            cluster_domain=domain,
            checkpoint_dir=_strip_or_none(settings.FLINK_OPERATOR_CHECKPOINT_DIR),
            image_pull_secrets=_strip_or_none(settings.FLINK_OPERATOR_IMAGE_PULL_SECRETS),
            s3_auth_mode=None,
            s3_access_key_id=None,
            s3_secret_access_key=None,
            s3_session_token=None,
            s3_region=_strip_or_none(getattr(settings, "GIDO_ARTIFACT_S3_REGION", None)),
            s3_endpoint_url=_strip_or_none(getattr(settings, "GIDO_ARTIFACT_S3_ENDPOINT_URL", None)),
            jar_s3_prefix=jar_prefix,
        )

    def with_overrides(
        self,
        *,
        namespace: Optional[str] = None,
        image: Optional[str] = None,
        flink_version: Optional[str] = None,
        service_account: Optional[str] = None,
        k8s_context: Optional[str] = None,
        kubeconfig_path: Optional[str] = None,
        jm_rest_template: Optional[str] = None,
        cluster_domain: Optional[str] = None,
        checkpoint_dir: Optional[str] = None,
        image_pull_secrets: Optional[str] = None,
        s3_auth_mode: Optional[str] = None,
        s3_access_key_id: Optional[str] = None,
        s3_secret_access_key: Optional[str] = None,
        s3_session_token: Optional[str] = None,
        s3_region: Optional[str] = None,
        s3_endpoint_url: Optional[str] = None,
        jar_s3_prefix: Optional[str] = None,
        profile_id: Optional[int] = None,
        profile_name: Optional[str] = None,
    ) -> "OperatorRuntimeContext":
        return OperatorRuntimeContext(
            profile_id=profile_id if profile_id is not None else self.profile_id,
            profile_name=profile_name if profile_name is not None else self.profile_name,
            namespace=_first_non_empty(namespace, self.namespace) or self.namespace,
            image=_first_non_empty(image, self.image) or self.image,
            flink_version=_first_non_empty(flink_version, self.flink_version) or self.flink_version,
            service_account=_first_non_empty(service_account, self.service_account) or self.service_account,
            k8s_context=_first_non_empty(k8s_context, self.k8s_context),
            kubeconfig_path=_first_non_empty(kubeconfig_path, self.kubeconfig_path),
            jm_rest_template=_first_non_empty(jm_rest_template, self.jm_rest_template) or self.jm_rest_template,
            cluster_domain=_first_non_empty(cluster_domain, self.cluster_domain) or self.cluster_domain,
            checkpoint_dir=_first_non_empty(checkpoint_dir, self.checkpoint_dir),
            image_pull_secrets=_first_non_empty(image_pull_secrets, self.image_pull_secrets),
            s3_auth_mode=_first_non_empty(s3_auth_mode, self.s3_auth_mode),
            s3_access_key_id=_first_non_empty(s3_access_key_id, self.s3_access_key_id),
            s3_secret_access_key=_first_non_empty(s3_secret_access_key, self.s3_secret_access_key),
            s3_session_token=_first_non_empty(s3_session_token, self.s3_session_token),
            s3_region=_first_non_empty(s3_region, self.s3_region),
            s3_endpoint_url=_first_non_empty(s3_endpoint_url, self.s3_endpoint_url),
            jar_s3_prefix=_first_non_empty(jar_s3_prefix, self.jar_s3_prefix),
        )

    def public_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "namespace": self.namespace,
            "image": self.image,
            "flink_version": self.flink_version,
            "service_account": self.service_account,
            "k8s_context": self.k8s_context,
            "kubeconfig_path": self.kubeconfig_path,
            "jm_rest_template": self.jm_rest_template,
            "cluster_domain": self.cluster_domain,
            "checkpoint_dir": self.checkpoint_dir,
            "image_pull_secrets": self.image_pull_secrets,
            "s3_auth_mode": self.s3_auth_mode,
            "s3_credentials_configured": bool(self.s3_access_key_id and self.s3_secret_access_key),
            "s3_region": self.s3_region,
            "s3_endpoint_url": self.s3_endpoint_url,
            "jar_s3_prefix": self.jar_s3_prefix,
        }


def _job_runtime_overrides(extra: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    if not extra or not isinstance(extra, dict):
        return {}
    return {
        "image": _first_non_empty(
            extra.get("operator_runtime_image"),
            extra.get("runtime_image"),
        ),
        "flink_version": _first_non_empty(
            extra.get("operator_flink_version"),
            extra.get("flink_version"),
        ),
    }


def resolve_operator_runtime(
    db: Session,
    workspace_id: int,
    profile_id: Optional[int] = None,
    streaming_properties: Optional[Dict[str, Any]] = None,
) -> OperatorRuntimeContext:
    """环境变量默认 → Operator Profile → 作业 streaming_properties 镜像/version 覆盖。"""
    base = OperatorRuntimeContext.from_settings()
    profile = None
    if profile_id is not None:
        from app.models.workspace import FlinkOperatorProfile

        profile = (
            db.query(FlinkOperatorProfile)
            .filter(
                FlinkOperatorProfile.id == int(profile_id),
                FlinkOperatorProfile.workspace_id == int(workspace_id),
                FlinkOperatorProfile.is_enabled.is_(True),
            )
            .first()
        )
        if profile is None:
            raise ValueError(f"Flink Operator 集群配置 #{profile_id} 不存在或未启用")
        base = base.with_overrides(
            profile_id=int(profile.id),
            profile_name=(profile.name or "").strip() or f"#{profile.id}",
            namespace=profile.flink_operator_namespace,
            image=profile.flink_operator_image,
            flink_version=profile.flink_operator_flink_version,
            service_account=profile.flink_operator_service_account,
            k8s_context=profile.flink_k8s_context,
            kubeconfig_path=profile.flink_k8s_kubeconfig_path,
            jm_rest_template=profile.flink_operator_jm_rest_template,
            cluster_domain=profile.flink_k8s_cluster_domain,
            checkpoint_dir=profile.flink_operator_checkpoint_dir,
            image_pull_secrets=profile.flink_operator_image_pull_secrets,
            s3_auth_mode=profile.flink_operator_s3_auth_mode,
            s3_access_key_id=profile.flink_operator_s3_access_key_id,
            s3_secret_access_key=profile.flink_operator_s3_secret_access_key,
            s3_session_token=profile.flink_operator_s3_session_token,
            s3_region=getattr(profile, "flink_operator_s3_region", None),
            s3_endpoint_url=getattr(profile, "flink_operator_s3_endpoint_url", None),
            jar_s3_prefix=profile.flink_operator_jar_s3_prefix,
        )
    overrides = _job_runtime_overrides(streaming_properties)
    explicit_version = bool(
        (profile is not None and _strip_or_none(getattr(profile, "flink_operator_flink_version", None)))
        or _strip_or_none(overrides.get("flink_version"))
    )
    ctx = base.with_overrides(
        image=overrides.get("image"),
        flink_version=overrides.get("flink_version"),
    )
    if not explicit_version:
        inferred = infer_operator_flink_version_from_image(ctx.image)
        if inferred:
            ctx = ctx.with_overrides(flink_version=inferred)
    return ctx


def resolve_operator_runtime_for_job(db: Session, job: Any) -> OperatorRuntimeContext:
    extra = None
    raw = getattr(job, "streaming_properties", None)
    if raw is not None and str(raw).strip():
        import json

        try:
            parsed = json.loads(str(raw))
            extra = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            extra = None
    profile_id = getattr(job, "flink_operator_profile_id", None)
    submit_ns = _strip_or_none(getattr(job, "flink_operator_submit_namespace", None))
    ctx = resolve_operator_runtime(
        db,
        int(getattr(job, "workspace_id") or 0),
        profile_id=int(profile_id) if profile_id is not None else None,
        streaming_properties=extra,
    )
    if submit_ns:
        ctx = ctx.with_overrides(namespace=submit_ns)
    return ctx


def list_runtime_images_for_workspace(db: Session, workspace_id: int) -> List[Dict[str, Any]]:
    """供 UI 下拉：全局默认 + 各 Profile 镜像。"""
    from app.models.workspace import FlinkOperatorProfile

    images: List[Dict[str, Any]] = []
    default = OperatorRuntimeContext.from_settings()
    images.append(
        {
            "source": "platform_default",
            "profile_id": None,
            "label": "平台默认",
            "image": default.image,
            "flink_version": default.flink_version,
        }
    )
    rows = (
        db.query(FlinkOperatorProfile)
        .filter(
            FlinkOperatorProfile.workspace_id == int(workspace_id),
            FlinkOperatorProfile.is_enabled.is_(True),
        )
        .order_by(FlinkOperatorProfile.id.asc())
        .all()
    )
    seen = {default.image}
    for p in rows:
        img = _strip_or_none(p.flink_operator_image)
        if not img or img in seen:
            continue
        seen.add(img)
        prof_ver = _strip_or_none(p.flink_operator_flink_version)
        if not prof_ver:
            prof_ver = infer_operator_flink_version_from_image(img)
        images.append(
            {
                "source": "profile",
                "profile_id": p.id,
                "label": (p.name or "").strip() or f"#{p.id}",
                "image": img,
                "flink_version": prof_ver or default.flink_version,
            }
        )
    return images
