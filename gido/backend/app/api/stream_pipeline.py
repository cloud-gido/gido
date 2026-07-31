# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Pipeline foundation APIs: profiles, schemas, compile/preflight and deployment groups."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, event, inspect as sa_inspect
from sqlalchemy.orm import Session

from app.core import perm_codes as PC
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.workspace import User
from app.services.rbac import (
    assert_workspace_data_capability,
    require_streaming_job,
    workspace_data_full_control,
)
from app.services.stream_pipeline_compiler import compile_pipeline
from app.services.stream_pipeline_runtime import (
    resolve_connection_profile_secrets,
    resolve_pipeline_sql_for_runtime,
    validate_secret_option_key,
)
from app.services.stream_pipeline_schema import diff_schema, preflight_pipeline
from app.services.stream_pipeline_spec import PipelineSpec

router = APIRouter(prefix="/streaming/pipeline", tags=["实时管道"])

_PROFILE_OPTION_WHITELIST = {
    "kafka": {
        "bootstrap.servers",
        "client.id",
        "sasl.mechanism",
        "schema.registry.url",
        "security.protocol",
    },
    "paimon": {"allowed.namespaces", "metastore", "uri", "warehouse"},
}


class StreamConnectionProfile(Base):
    __tablename__ = "dw_stream_connection_profiles"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_stream_conn_ws_name"),)
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    connector_type = Column(String(32), nullable=False)
    options = Column(JSON, nullable=False, default=dict)
    secret_refs = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StreamSchemaContract(Base):
    __tablename__ = "dw_stream_schema_contracts"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_stream_schema_ws_name"),)
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    compatibility_mode = Column(String(32), nullable=False, default="backward")
    description = Column(Text, nullable=True)
    current_version_id = Column(Integer, nullable=True)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StreamSchemaVersion(Base):
    __tablename__ = "dw_stream_schema_versions"
    __table_args__ = (
        UniqueConstraint("contract_id", "version", name="uq_stream_schema_contract_version"),
        Index("idx_stream_schema_contract_created", "contract_id", "created_at"),
    )
    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("dw_stream_schema_contracts.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    fields = Column(JSON, nullable=False)
    schema_hash = Column(String(64), nullable=False)
    change_note = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


@event.listens_for(StreamSchemaVersion, "before_update")
def _immutable_schema_version(mapper, connection, target) -> None:
    if any(sa_inspect(target).attrs[name].history.has_changes() for name in ("contract_id", "version", "fields", "schema_hash")):
        raise ValueError("stream schema version is immutable")


class StreamSchemaEvolutionAudit(Base):
    __tablename__ = "dw_stream_schema_evolution_audits"
    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("dw_stream_schema_contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    from_version_id = Column(Integer, nullable=True)
    to_version_id = Column(Integer, nullable=False)
    compatibility = Column(String(32), nullable=False)
    diff = Column(JSON, nullable=False)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class StreamDeploymentGroup(Base):
    __tablename__ = "dw_stream_deployment_groups"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_stream_group_ws_name"),)
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    strategy = Column(String(32), nullable=False, default="ordered")
    security_domain = Column(String(64), nullable=False, default="default")
    runtime_version = Column(String(32), nullable=False, default="2.0.1")
    checkpoint_backend = Column(String(64), nullable=False, default="filesystem")
    custom_dependencies = Column(JSON, nullable=False, default=list)
    capacity_slots = Column(Integer, nullable=False, default=4)
    allows_stateful = Column(Boolean, nullable=False, default=True)
    highest_sla_tier = Column(String(32), nullable=False, default="standard")
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StreamDeploymentGroupMember(Base):
    __tablename__ = "dw_stream_deployment_group_members"
    __table_args__ = (UniqueConstraint("group_id", "job_id", name="uq_stream_group_job"),)
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("dw_stream_deployment_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("dw_streaming_jobs.id", ondelete="CASCADE"), nullable=False)
    deploy_order = Column(Integer, nullable=False, default=0)
    required = Column(Boolean, nullable=False, default=True)


class StreamPipelineSloPolicy(Base):
    __tablename__ = "dw_stream_pipeline_slo_policies"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_stream_pipeline_slo_job"),
    )
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=False, index=True)
    job_id = Column(
        Integer, ForeignKey("dw_streaming_jobs.id", ondelete="CASCADE"), nullable=False
    )
    max_consumer_lag = Column(Integer, nullable=False, default=100000)
    max_checkpoint_duration_ms = Column(Integer, nullable=False, default=120000)
    max_restart_count = Column(Integer, nullable=False, default=3)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ConnectionProfileBody(BaseModel):
    workspace_id: int
    name: str = Field(min_length=1, max_length=128)
    connector_type: str
    options: Dict[str, Any] = Field(default_factory=dict)
    secret_refs: Dict[str, str] = Field(default_factory=dict)
    is_active: bool = True


class SchemaContractBody(BaseModel):
    workspace_id: int
    name: str = Field(min_length=1, max_length=128)
    compatibility_mode: str = "backward"
    description: Optional[str] = None


class SchemaVersionBody(BaseModel):
    fields: List[Dict[str, Any]]
    change_note: Optional[str] = None
    allow_breaking: bool = False


class DeploymentGroupBody(BaseModel):
    workspace_id: int
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = None
    strategy: str = "ordered"
    security_domain: str = "default"
    runtime_version: str = "2.0.1"
    checkpoint_backend: str = "filesystem"
    custom_dependencies: List[str] = Field(default_factory=list)
    capacity_slots: int = Field(default=4, ge=1, le=10000)
    allows_stateful: bool = True
    highest_sla_tier: str = "standard"


class DeploymentMemberBody(BaseModel):
    job_id: int
    deploy_order: int = 0
    required: bool = True


class PlacementPreviewBody(BaseModel):
    workspace_id: int
    job_id: Optional[int] = None
    requested_mode: str = "recommend-only"
    sla_tier: str = "standard"
    stateful: bool = False
    state_size_gb: float = Field(default=0, ge=0)
    security_domain: str = "default"
    runtime_version: str = "2.0.1"
    checkpoint_backend: str = "filesystem"
    custom_dependencies: List[str] = Field(default_factory=list)
    parallelism: int = Field(default=1, ge=1)
    expected_records_per_second: int = Field(default=0, ge=0)
    existing_group_id: Optional[str] = None


class SloPolicyBody(BaseModel):
    max_consumer_lag: int = Field(default=100000, ge=0)
    max_checkpoint_duration_ms: int = Field(default=120000, ge=1)
    max_restart_count: int = Field(default=3, ge=0)
    enabled: bool = True


def _profile_public(row: StreamConnectionProfile) -> dict:
    return {
        "id": row.id, "workspace_id": row.workspace_id, "name": row.name,
        "connector_type": row.connector_type, "options": row.options or {},
        "secret_ref_keys": sorted((row.secret_refs or {}).keys()),
        "is_active": bool(row.is_active), "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _validate_profile_body(body: ConnectionProfileBody) -> None:
    allowed = _PROFILE_OPTION_WHITELIST.get(body.connector_type)
    if allowed is None:
        raise HTTPException(422, "connector_type 须为 kafka 或 paimon")
    sensitive = sorted(
        key for key in body.options
        if any(marker in key.lower() for marker in ("password", "secret", "token", "credential", "api.key"))
    )
    if sensitive:
        raise HTTPException(422, f"敏感连接选项必须使用 secret_refs: {sensitive}")
    embedded_credentials = sorted(
        key
        for key, value in body.options.items()
        if re.search(r"[a-z][a-z0-9+.-]*://[^/@:\s]+:[^/@\s]+@", str(value), re.I)
    )
    if embedded_credentials:
        raise HTTPException(
            422, f"连接 URL 不得内嵌凭据，必须使用 secret_refs: {embedded_credentials}"
        )
    rejected = sorted(set(body.options) - allowed)
    if rejected:
        raise HTTPException(422, f"不支持的 {body.connector_type} 连接选项: {rejected}")
    for option_key, variable_key in body.secret_refs.items():
        try:
            validate_secret_option_key(body.connector_type, option_key)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not str(variable_key or "").strip():
            raise HTTPException(422, f"secret_refs.{option_key} 必须引用工作空间变量")


def _profile(db: Session, user: User, profile_id: int, write: bool = False) -> StreamConnectionProfile:
    query = db.query(StreamConnectionProfile).filter_by(id=profile_id)
    row = (query.with_for_update() if write else query).first()
    if not row:
        raise HTTPException(404, "连接配置不存在")
    assert_workspace_data_capability(
        db,
        user,
        row.workspace_id,
        "developer" if write else "viewer",
        PC.GIDO_STREAM_WRITE if write else PC.GIDO_STREAM_READ,
    )
    return row


def _profile_release_references(db: Session, profile_id: int) -> List[int]:
    from app.api.streaming import StreamingJobRelease

    release_ids: List[int] = []
    for release in (
        db.query(StreamingJobRelease)
        .filter(StreamingJobRelease.definition_kind == "pipeline")
        .all()
    ):
        spec = release.pipeline_spec or {}
        source_id = (spec.get("source") or {}).get("connection_profile_id")
        sink_id = (spec.get("sink") or {}).get("connection_profile_id")
        if int(source_id or 0) == profile_id or int(sink_id or 0) == profile_id:
            release_ids.append(int(release.id))
    return release_ids


@router.get("/connection-profiles")
def list_connection_profiles(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_STREAM_READ)
    return [_profile_public(row) for row in db.query(StreamConnectionProfile).filter_by(workspace_id=workspace_id).order_by(StreamConnectionProfile.id).all()]


@router.post("/connection-profiles")
def create_connection_profile(body: ConnectionProfileBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    _validate_profile_body(body)
    row = StreamConnectionProfile(**body.model_dump(), created_by=current_user.id)
    db.add(row); db.commit(); db.refresh(row)
    return _profile_public(row)


@router.put("/connection-profiles/{profile_id}")
def update_connection_profile(profile_id: int, body: ConnectionProfileBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _profile(db, current_user, profile_id, True)
    references = _profile_release_references(db, profile_id)
    if references:
        raise HTTPException(
            409,
            {
                "message": "连接配置已被不可变发布版本引用，请创建新配置",
                "release_ids": references[:50],
            },
        )
    _validate_profile_body(body)
    if body.workspace_id != row.workspace_id:
        raise HTTPException(409, "不可移动连接配置到其他工作空间")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    db.commit(); db.refresh(row)
    return _profile_public(row)


@router.delete("/connection-profiles/{profile_id}")
def delete_connection_profile(profile_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _profile(db, current_user, profile_id, True)
    references = _profile_release_references(db, profile_id)
    if references:
        raise HTTPException(
            409,
            {
                "message": "连接配置已被不可变发布版本引用，不能删除",
                "release_ids": references[:50],
            },
        )
    db.delete(row); db.commit()
    return {"deleted": True, "id": profile_id}


@router.post("/connection-profiles/{profile_id}/discover")
def discover_connection_profile(profile_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _profile(db, current_user, profile_id)
    if row.connector_type == "kafka":
        from app.services.stream_observability import KafkaPythonOffsetsProvider, redact_text
        from app.services.stream_pipeline_schema import (
            discover_schema_registry_subjects,
        )

        try:
            secrets = resolve_connection_profile_secrets(
                db,
                workspace_id=row.workspace_id,
                connector_type="kafka",
                secret_refs=row.secret_refs or {},
            )
            properties = {
                "properties.security.protocol": (row.options or {}).get("security.protocol"),
                "properties.sasl.mechanism": (row.options or {}).get("sasl.mechanism"),
                **secrets,
            }
            provider = KafkaPythonOffsetsProvider(
                bootstrap_servers=str((row.options or {}).get("bootstrap.servers") or ""),
                properties={key: value for key, value in properties.items() if value},
            )
            registry_url = str(
                (row.options or {}).get("schema.registry.url") or ""
            ).strip()
            registry_result: Dict[str, Any] = {
                "configured": bool(registry_url),
                "status": "not_configured",
                "subjects": [],
            }
            if registry_url:
                try:
                    registry_result = {
                        "configured": True,
                        "status": "available",
                        "subjects": discover_schema_registry_subjects(
                            registry_url,
                            basic_auth=secrets.get(
                                "schema.registry.basic.auth.user.info"
                            ),
                        ),
                    }
                except Exception as exc:
                    registry_result = {
                        "configured": True,
                        "status": "unavailable",
                        "subjects": [],
                        "error": redact_text(exc),
                    }
            return {
                "profile_id": row.id,
                "status": "available",
                "resource_type": "topic",
                "resources": provider.discover_topics(),
                "schema_registry": registry_result,
                "network_access": True,
            }
        except Exception as exc:
            return {
                "profile_id": row.id,
                "status": "unavailable",
                "resource_type": "topic",
                "resources": [],
                "network_access": True,
                "error": redact_text(exc),
            }
    from app.services.stream_observability import redact_text
    from app.services.stream_pipeline_schema import discover_paimon_tables

    try:
        return {
            "profile_id": row.id,
            "status": "available",
            "resource_type": "paimon_table",
            "resources": discover_paimon_tables(row.options or {}),
            "network_access": True,
            "warehouse": (row.options or {}).get("warehouse"),
        }
    except Exception as exc:
        return {
            "profile_id": row.id,
            "status": "unavailable",
            "resource_type": "paimon_table",
            "resources": [],
            "network_access": True,
            "warehouse": (row.options or {}).get("warehouse"),
            "error": redact_text(exc),
        }


@router.post("/compile")
def compile_pipeline_spec(
    spec: PipelineSpec,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source = _profile(db, current_user, spec.source.connection_profile_id)
    sink = _profile(db, current_user, spec.sink.connection_profile_id)
    if source.workspace_id != sink.workspace_id:
        raise HTTPException(409, "source/sink 连接配置不属于同一工作空间")
    return compile_pipeline(spec)


def _profile_types(db: Session, spec: PipelineSpec) -> dict[int, str]:
    ids = {spec.source.connection_profile_id, spec.sink.connection_profile_id}
    return {row.id: row.connector_type for row in db.query(StreamConnectionProfile).filter(StreamConnectionProfile.id.in_(ids), StreamConnectionProfile.is_active.is_(True)).all()}


def _schema_baseline(db: Session, spec: PipelineSpec, workspace_id: int) -> Optional[List[Dict[str, Any]]]:
    if spec.schema_contract_id is None:
        return None
    contract = db.query(StreamSchemaContract).filter_by(id=spec.schema_contract_id).first()
    if not contract:
        raise HTTPException(422, "pipeline 引用的 schema contract 不存在")
    if contract.workspace_id != workspace_id:
        raise HTTPException(409, "schema contract 与 pipeline 不属于同一工作空间")
    query = db.query(StreamSchemaVersion).filter_by(contract_id=contract.id)
    version = (
        query.filter_by(version=spec.schema_version).first()
        if spec.schema_version is not None
        else query.order_by(StreamSchemaVersion.version.desc()).first()
    )
    if not version:
        raise HTTPException(422, "pipeline 引用的 schema version 不存在")
    return version.fields


def _preflight_with_runtime(
    db: Session,
    spec: PipelineSpec,
    *,
    workspace_id: int,
    previous_schema: Optional[List[Dict[str, Any]]] = None,
) -> dict:
    result = preflight_pipeline(
        spec,
        previous_schema=(
            previous_schema
            if previous_schema is not None
            else _schema_baseline(db, spec, workspace_id)
        ),
        connection_profiles=_profile_types(db, spec),
    )
    try:
        resolve_pipeline_sql_for_runtime(
            db,
            workspace_id=workspace_id,
            spec=spec,
        )
        runtime_check = {
            "check": "runtime_profile_resolution",
            "status": "passed",
            "detail": None,
        }
    except (ValueError, TypeError) as exc:
        runtime_check = {
            "check": "runtime_profile_resolution",
            "status": "failed",
            "detail": str(exc),
        }
    result["checks"].append(runtime_check)
    result["ok"] = bool(result["ok"] and runtime_check["status"] == "passed")
    return result


def _job_previous_release_schema(
    db: Session,
    job: Any,
) -> Optional[List[Dict[str, Any]]]:
    from app.api.streaming import StreamingJobRelease

    release = None
    approved_id = getattr(job, "current_approved_release_id", None)
    if approved_id:
        release = (
            db.query(StreamingJobRelease)
            .filter_by(id=approved_id, job_id=job.id)
            .first()
        )
    if not release:
        release = (
            db.query(StreamingJobRelease)
            .filter_by(job_id=job.id, approval_status="approved")
            .order_by(StreamingJobRelease.version.desc())
            .first()
        )
    schema = ((release.pipeline_spec or {}).get("schema") if release else None)
    return schema if isinstance(schema, list) else None


@router.post("/preflight")
def preflight_pipeline_spec(spec: PipelineSpec, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    source = _profile(db, current_user, spec.source.connection_profile_id)
    sink = _profile(db, current_user, spec.sink.connection_profile_id)
    if source.workspace_id != sink.workspace_id:
        raise HTTPException(409, "source/sink 连接配置不属于同一工作空间")
    return _preflight_with_runtime(db, spec, workspace_id=source.workspace_id)


@router.put("/jobs/{job_id}/spec")
def save_job_pipeline_spec(job_id: int, spec: PipelineSpec, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE)
    for profile_id in (spec.source.connection_profile_id, spec.sink.connection_profile_id):
        profile = _profile(db, current_user, profile_id)
        if profile.workspace_id != job.workspace_id:
            raise HTTPException(409, "连接配置与作业不属于同一工作空间")
    check = _preflight_with_runtime(
        db,
        spec,
        workspace_id=job.workspace_id,
        previous_schema=_job_previous_release_schema(db, job),
    )
    if not check["ok"]:
        raise HTTPException(422, check)
    artifact = compile_pipeline(spec)
    job.definition_kind = "pipeline"
    job.pipeline_spec = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    job.compiler_version = artifact["compiler_version"]
    job.generated_artifact = artifact
    job.spec_hash = artifact["spec_hash"]
    job.script_content = artifact["sql"]
    job.updated_at = datetime.utcnow()
    db.commit()
    return {"job_id": job.id, "definition_kind": "pipeline", "pipeline_spec": job.pipeline_spec, "compiler_version": job.compiler_version, "generated_artifact": job.generated_artifact, "spec_hash": job.spec_hash}


@router.post("/jobs/{job_id}/compile")
def compile_job_pipeline(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE)
    if not job.pipeline_spec:
        raise HTTPException(409, "作业没有 pipeline_spec")
    return save_job_pipeline_spec(job_id, PipelineSpec.model_validate(job.pipeline_spec), db, current_user)


@router.post("/jobs/{job_id}/preflight")
def preflight_job_pipeline(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = require_streaming_job(db, current_user, job_id, "viewer", PC.GIDO_STREAM_READ)
    if not job.pipeline_spec:
        raise HTTPException(409, "作业没有 pipeline_spec")
    spec = PipelineSpec.model_validate(job.pipeline_spec)
    return _preflight_with_runtime(
        db,
        spec,
        workspace_id=job.workspace_id,
        previous_schema=_job_previous_release_schema(db, job),
    )


@router.get("/jobs/{job_id}/observability")
def get_pipeline_observability(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.stream_observability import (
        FlinkRestObservabilityAdapter,
        KafkaLagAdapter,
        KafkaPythonOffsetsProvider,
        PaimonCommitAdapter,
        PrometheusQueryAdapter,
        S3PaimonCommitProvider,
        unavailable_observation,
    )

    job = require_streaming_job(
        db, current_user, job_id, "viewer", PC.GIDO_STREAM_READ
    )
    if getattr(job, "definition_kind", None) != "pipeline" or not job.pipeline_spec:
        raise HTTPException(409, "该作业不是数据管道")
    spec = PipelineSpec.model_validate(job.pipeline_spec)
    source = _profile(db, current_user, spec.source.connection_profile_id)
    sink = _profile(db, current_user, spec.sink.connection_profile_id)
    observations = []
    try:
        secrets = resolve_connection_profile_secrets(
            db,
            workspace_id=job.workspace_id,
            connector_type="kafka",
            secret_refs=source.secret_refs or {},
        )
        properties = {
            "properties.security.protocol": (source.options or {}).get(
                "security.protocol"
            ),
            "properties.sasl.mechanism": (source.options or {}).get(
                "sasl.mechanism"
            ),
            **secrets,
        }
        kafka_provider = KafkaPythonOffsetsProvider(
            bootstrap_servers=str(
                (source.options or {}).get("bootstrap.servers") or ""
            ),
            properties={key: value for key, value in properties.items() if value},
        )
        kafka = KafkaLagAdapter(kafka_provider).observe(
            topic=spec.source.topic,
            group_id=spec.source.consumer_group,
        )
    except Exception as exc:
        kafka = unavailable_observation("kafka", exc)
    observations.append(asdict(kafka))

    flink = FlinkRestObservabilityAdapter(
        str(getattr(job, "flink_application_jm_rest", None) or "")
    ).observe(job_id=str(getattr(job, "flink_job_id", None) or ""))
    observations.append(asdict(flink))
    prometheus = PrometheusQueryAdapter(
        str(getattr(settings, "STREAM_PROMETHEUS_URL", None) or "")
    ).observe(
        queries={
            "records_in_rate": (
                f'flink_taskmanager_job_task_operator_numRecordsInPerSecond'
                f'{{job_id="{str(job.flink_job_id or "")}"}}'
            ),
            "records_out_rate": (
                f'flink_taskmanager_job_task_operator_numRecordsOutPerSecond'
                f'{{job_id="{str(job.flink_job_id or "")}"}}'
            ),
        }
    )
    observations.append(asdict(prometheus))
    try:
        paimon = PaimonCommitAdapter(
            S3PaimonCommitProvider(str((sink.options or {}).get("warehouse") or ""))
        ).observe(
            table_identifier=f"{spec.sink.database}.{spec.sink.table}",
            limit=20,
        )
    except Exception as exc:
        paimon = unavailable_observation("paimon", exc)
    observations.append(asdict(paimon))
    policy = db.query(StreamPipelineSloPolicy).filter_by(job_id=job.id).first()
    lag_slo = None
    if policy and policy.enabled:
        from app.services.stream_observability import summarize_lag_slo

        lag_slo = asdict(
            summarize_lag_slo(
                kafka, maximum_lag=int(policy.max_consumer_lag)
            )
        )
    return {
        "job_id": job.id,
        "observations": observations,
        "slo": {"kafka_lag": lag_slo} if lag_slo else {},
        "storage": "read-through",
        "time_series_persisted_in_gido": False,
    }


@router.get("/jobs/{job_id}/slo-policy")
def get_pipeline_slo_policy(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = require_streaming_job(
        db, current_user, job_id, "viewer", PC.GIDO_STREAM_READ
    )
    row = db.query(StreamPipelineSloPolicy).filter_by(job_id=job.id).first()
    return row or {
        "job_id": job.id,
        "workspace_id": job.workspace_id,
        **SloPolicyBody().model_dump(),
        "inherited_default": True,
    }


@router.put("/jobs/{job_id}/slo-policy")
def put_pipeline_slo_policy(
    job_id: int,
    body: SloPolicyBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = require_streaming_job(
        db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE
    )
    if getattr(job, "definition_kind", None) != "pipeline":
        raise HTTPException(409, "该作业不是数据管道")
    row = db.query(StreamPipelineSloPolicy).filter_by(job_id=job.id).first()
    if not row:
        row = StreamPipelineSloPolicy(
            workspace_id=job.workspace_id,
            job_id=job.id,
        )
        db.add(row)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    row.updated_by = current_user.id
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def _contract(db: Session, user: User, contract_id: int, write: bool = False) -> StreamSchemaContract:
    row = db.query(StreamSchemaContract).filter_by(id=contract_id).first()
    if not row:
        raise HTTPException(404, "Schema contract 不存在")
    assert_workspace_data_capability(
        db,
        user,
        row.workspace_id,
        "developer" if write else "viewer",
        PC.GIDO_STREAM_WRITE if write else PC.GIDO_STREAM_READ,
    )
    return row


def _contract_release_references(db: Session, contract_id: int) -> List[int]:
    from app.api.streaming import StreamingJobRelease

    return [
        int(release.id)
        for release in (
            db.query(StreamingJobRelease)
            .filter(StreamingJobRelease.definition_kind == "pipeline")
            .all()
        )
        if int((release.pipeline_spec or {}).get("schema_contract_id") or 0)
        == contract_id
    ]


@router.get("/schema-contracts")
def list_schema_contracts(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_STREAM_READ)
    return db.query(StreamSchemaContract).filter_by(workspace_id=workspace_id).order_by(StreamSchemaContract.id).all()


@router.post("/schema-contracts")
def create_schema_contract(body: SchemaContractBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    if body.compatibility_mode not in ("backward", "none"):
        raise HTTPException(422, "compatibility_mode 须为 backward 或 none")
    if body.compatibility_mode == "none" and not workspace_data_full_control(
        db, current_user, body.workspace_id
    ):
        raise HTTPException(403, "关闭 Schema 兼容性检查仅限工作空间管理员")
    row = StreamSchemaContract(**body.model_dump(), created_by=current_user.id)
    db.add(row); db.commit(); db.refresh(row)
    return row


@router.put("/schema-contracts/{contract_id}")
def update_schema_contract(contract_id: int, body: SchemaContractBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _contract(db, current_user, contract_id, True)
    if body.workspace_id != row.workspace_id:
        raise HTTPException(409, "不可移动 schema contract 到其他工作空间")
    if body.compatibility_mode not in ("backward", "none"):
        raise HTTPException(422, "compatibility_mode 须为 backward 或 none")
    if body.compatibility_mode == "none" and not workspace_data_full_control(
        db, current_user, row.workspace_id
    ):
        raise HTTPException(403, "关闭 Schema 兼容性检查仅限工作空间管理员")
    row.name, row.description, row.compatibility_mode = body.name, body.description, body.compatibility_mode
    db.commit(); db.refresh(row)
    return row


@router.delete("/schema-contracts/{contract_id}")
def delete_schema_contract(contract_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _contract(db, current_user, contract_id, True)
    references = _contract_release_references(db, contract_id)
    if references:
        raise HTTPException(
            409,
            {
                "message": "Schema Contract 已被不可变发布版本引用，不能删除",
                "release_ids": references[:50],
            },
        )
    db.delete(row); db.commit()
    return {"deleted": True, "id": contract_id}


@router.get("/schema-contracts/{contract_id}/versions")
def list_schema_versions(contract_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _contract(db, current_user, contract_id)
    return db.query(StreamSchemaVersion).filter_by(contract_id=contract_id).order_by(StreamSchemaVersion.version.desc()).all()


@router.post("/schema-contracts/{contract_id}/versions")
def create_schema_version(contract_id: int, body: SchemaVersionBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    import hashlib, json
    contract = _contract(db, current_user, contract_id, True)
    previous = db.query(StreamSchemaVersion).filter_by(contract_id=contract_id).order_by(StreamSchemaVersion.version.desc()).first()
    canonical = json.dumps(body.fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    schema_hash = hashlib.sha256(canonical.encode()).hexdigest()
    if previous and previous.schema_hash == schema_hash:
        return {
            "version": previous,
            "diff": {"compatible": True, "changes": []},
            "unchanged": True,
        }
    comparison = diff_schema(previous.fields, body.fields) if previous else {"compatible": True, "changes": []}
    if not comparison["compatible"] and not body.allow_breaking:
        raise HTTPException(409, {"message": "breaking schema evolution", "diff": comparison})
    if (
        not comparison["compatible"]
        and body.allow_breaking
        and not workspace_data_full_control(db, current_user, contract.workspace_id)
    ):
        raise HTTPException(
            403, "接受 breaking Schema 演进仅限工作空间管理员"
        )
    row = StreamSchemaVersion(contract_id=contract_id, version=(previous.version + 1 if previous else 1), fields=body.fields, schema_hash=schema_hash, change_note=body.change_note, created_by=current_user.id)
    db.add(row); db.flush()
    audit = StreamSchemaEvolutionAudit(contract_id=contract_id, from_version_id=previous.id if previous else None, to_version_id=row.id, compatibility="compatible" if comparison["compatible"] else "breaking_accepted", diff=comparison, created_by=current_user.id)
    db.add(audit); contract.current_version_id = row.id; db.commit(); db.refresh(row)
    return {"version": row, "diff": comparison}


@router.post("/schema-contracts/{contract_id}/diff")
def schema_contract_diff(contract_id: int, body: SchemaVersionBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _contract(db, current_user, contract_id)
    previous = db.query(StreamSchemaVersion).filter_by(contract_id=contract_id).order_by(StreamSchemaVersion.version.desc()).first()
    return diff_schema(previous.fields if previous else [], body.fields)


@router.get("/schema-contracts/{contract_id}/evolution-audits")
def list_schema_evolution_audits(contract_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _contract(db, current_user, contract_id)
    return db.query(StreamSchemaEvolutionAudit).filter_by(contract_id=contract_id).order_by(StreamSchemaEvolutionAudit.id.desc()).all()


@router.get("/deployment-groups")
def list_deployment_groups(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_STREAM_READ)
    return db.query(StreamDeploymentGroup).filter_by(workspace_id=workspace_id).order_by(StreamDeploymentGroup.id).all()


@router.post("/placement/preview")
def preview_pipeline_placement(
    body: PlacementPreviewBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.api.streaming import StreamingJob
    from app.services.stream_placement_policy import (
        JobPlacementRequirements,
        PlacementGroup,
        PlacementMode,
        decide_placement,
    )

    assert_workspace_data_capability(
        db, current_user, body.workspace_id, "viewer", PC.GIDO_STREAM_READ
    )
    try:
        requested_mode = PlacementMode(body.requested_mode)
    except ValueError as exc:
        raise HTTPException(
            422, "requested_mode 须为 dedicated、grouped 或 recommend-only"
        ) from exc
    groups = (
        db.query(StreamDeploymentGroup)
        .filter_by(workspace_id=body.workspace_id)
        .order_by(StreamDeploymentGroup.id)
        .all()
    )
    members = (
        db.query(StreamDeploymentGroupMember)
        .filter(StreamDeploymentGroupMember.group_id.in_([row.id for row in groups]))
        .all()
        if groups
        else []
    )
    job_ids = {int(member.job_id) for member in members}
    parallelism_by_job = {
        int(row.id): int(row.parallelism or 1)
        for row in (
            db.query(StreamingJob)
            .filter(StreamingJob.id.in_(job_ids))
            .all()
            if job_ids
            else []
        )
    }
    used_by_group: Dict[int, int] = {}
    for member in members:
        used_by_group[int(member.group_id)] = (
            used_by_group.get(int(member.group_id), 0)
            + parallelism_by_job.get(int(member.job_id), 1)
        )
    decision = decide_placement(
        JobPlacementRequirements(
            job_id=str(body.job_id or "draft"),
            requested_mode=requested_mode,
            sla_tier=body.sla_tier,
            stateful=body.stateful,
            state_size_gb=body.state_size_gb,
            security_domain=body.security_domain,
            runtime_version=body.runtime_version,
            checkpoint_backend=body.checkpoint_backend,
            custom_dependencies=tuple(sorted(body.custom_dependencies)),
            parallelism=body.parallelism,
            expected_records_per_second=body.expected_records_per_second,
            existing_group_id=body.existing_group_id,
        ),
        [
            PlacementGroup(
                group_id=str(row.id),
                security_domain=row.security_domain,
                runtime_version=row.runtime_version,
                checkpoint_backend=row.checkpoint_backend,
                custom_dependencies=tuple(sorted(row.custom_dependencies or [])),
                capacity_slots=int(row.capacity_slots or 1),
                used_slots=used_by_group.get(int(row.id), 0),
                allows_stateful=bool(row.allows_stateful),
                highest_sla_tier=row.highest_sla_tier,
            )
            for row in groups
        ],
    )
    return asdict(decision)


@router.post("/deployment-groups")
def create_deployment_group(body: DeploymentGroupBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    row = StreamDeploymentGroup(**body.model_dump(), created_by=current_user.id)
    db.add(row); db.commit(); db.refresh(row)
    return row


def _group(db: Session, user: User, group_id: int, write: bool = False) -> StreamDeploymentGroup:
    row = db.query(StreamDeploymentGroup).filter_by(id=group_id).first()
    if not row:
        raise HTTPException(404, "部署组不存在")
    assert_workspace_data_capability(
        db,
        user,
        row.workspace_id,
        "developer" if write else "viewer",
        PC.GIDO_STREAM_WRITE if write else PC.GIDO_STREAM_READ,
    )
    return row


@router.put("/deployment-groups/{group_id}")
def update_deployment_group(group_id: int, body: DeploymentGroupBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _group(db, current_user, group_id, True)
    if body.workspace_id != row.workspace_id:
        raise HTTPException(409, "不可移动部署组到其他工作空间")
    from app.api.streaming import StreamingJob

    running_member = (
        db.query(StreamDeploymentGroupMember)
        .join(StreamingJob, StreamingJob.id == StreamDeploymentGroupMember.job_id)
        .filter(
            StreamDeploymentGroupMember.group_id == group_id,
            StreamingJob.status == "running",
        )
        .first()
    )
    if running_member:
        raise HTTPException(
            409, "部署组包含运行中作业；请先 Savepoint 停止后再修改组策略"
        )
    for key, value in body.model_dump(exclude={"workspace_id"}).items():
        setattr(row, key, value)
    db.commit(); db.refresh(row)
    return row


@router.delete("/deployment-groups/{group_id}")
def delete_deployment_group(group_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _group(db, current_user, group_id, True)
    from app.api.streaming import StreamingJob

    running_member = (
        db.query(StreamDeploymentGroupMember)
        .join(StreamingJob, StreamingJob.id == StreamDeploymentGroupMember.job_id)
        .filter(
            StreamDeploymentGroupMember.group_id == group_id,
            StreamingJob.status == "running",
        )
        .first()
    )
    if running_member:
        raise HTTPException(
            409, "部署组包含运行中作业；请先 Savepoint 停止后再删除"
        )
    db.delete(row); db.commit()
    return {"deleted": True, "id": group_id}


@router.get("/deployment-groups/{group_id}/members")
def list_deployment_group_members(group_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _group(db, current_user, group_id)
    return db.query(StreamDeploymentGroupMember).filter_by(group_id=group_id).order_by(StreamDeploymentGroupMember.deploy_order, StreamDeploymentGroupMember.id).all()


@router.post("/deployment-groups/{group_id}/members")
def add_deployment_group_member(group_id: int, body: DeploymentMemberBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    group = _group(db, current_user, group_id, True)
    job = require_streaming_job(db, current_user, body.job_id, "developer", PC.GIDO_STREAM_WRITE)
    if job.workspace_id != group.workspace_id:
        raise HTTPException(409, "作业与部署组不属于同一工作空间")
    if str(job.status or "").lower() == "running":
        raise HTTPException(
            409, "运行中的作业不得自动改组；请先 Savepoint 停止后显式迁移"
        )
    row = StreamDeploymentGroupMember(group_id=group_id, **body.model_dump())
    db.add(row); db.commit(); db.refresh(row)
    return row


@router.delete("/deployment-groups/{group_id}/members/{member_id}")
def delete_deployment_group_member(group_id: int, member_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _group(db, current_user, group_id, True)
    row = db.query(StreamDeploymentGroupMember).filter_by(id=member_id, group_id=group_id).first()
    if not row:
        raise HTTPException(404, "部署组成员不存在")
    from app.api.streaming import StreamingJob

    job = db.query(StreamingJob).filter_by(id=row.job_id).first()
    if job and str(job.status or "").lower() == "running":
        raise HTTPException(
            409, "运行中的作业不得自动改组；请先 Savepoint 停止后显式迁移"
        )
    db.delete(row); db.commit()
    return {"deleted": True, "id": member_id}
