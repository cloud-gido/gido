# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""连接器 / 依赖文件制品库 API（镜像 JAR 制品模式）。"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import streaming as st
from app.core import perm_codes as PC
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import User
from app.services.rbac import assert_workspace_data_capability
from app.services.artifact_s3 import JAR_ARTIFACT_FILENAME


class ArtifactCreate(BaseModel):
    workspace_id: int
    name: str
    description: Optional[str] = None


class ArtifactUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


def _job_refs_connector(db: Session, version_id: int) -> int:
    n = 0
    for job in db.query(st.StreamingJob).all():
        if version_id in st._parse_version_id_list(getattr(job, "connector_version_ids", None)):
            n += 1
    for release in db.query(st.StreamingJobRelease).filter(
        st.StreamingJobRelease.approval_status.in_(("pending", "approved"))
    ).all():
        if version_id in st._parse_version_id_list(
            getattr(release, "connector_version_ids", None)
        ):
            n += 1
    return n


def _job_refs_file(db: Session, version_id: int) -> int:
    n = 0
    for job in db.query(st.StreamingJob).all():
        if version_id in st._parse_version_id_list(getattr(job, "dependency_file_version_ids", None)):
            n += 1
    for release in db.query(st.StreamingJobRelease).filter(
        st.StreamingJobRelease.approval_status.in_(("pending", "approved"))
    ).all():
        if version_id in st._parse_version_id_list(
            getattr(release, "dependency_file_version_ids", None)
        ):
            n += 1
    return n


def _job_refs_connector_artifact(db: Session, artifact_id: int) -> int:
    vers = (
        db.query(st.StreamingConnectorVersion.id)
        .filter(st.StreamingConnectorVersion.artifact_id == artifact_id)
        .all()
    )
    vids = {int(r[0]) for r in vers}
    if not vids:
        return 0
    n = 0
    for job in db.query(st.StreamingJob).all():
        if vids.intersection(st._parse_version_id_list(getattr(job, "connector_version_ids", None))):
            n += 1
    for release in db.query(st.StreamingJobRelease).filter(
        st.StreamingJobRelease.approval_status.in_(("pending", "approved"))
    ).all():
        if vids.intersection(
            st._parse_version_id_list(getattr(release, "connector_version_ids", None))
        ):
            n += 1
    return n


def _job_refs_file_artifact(db: Session, artifact_id: int) -> int:
    vers = (
        db.query(st.StreamingFileVersion.id)
        .filter(st.StreamingFileVersion.artifact_id == artifact_id)
        .all()
    )
    vids = {int(r[0]) for r in vers}
    if not vids:
        return 0
    n = 0
    for job in db.query(st.StreamingJob).all():
        if vids.intersection(st._parse_version_id_list(getattr(job, "dependency_file_version_ids", None))):
            n += 1
    for release in db.query(st.StreamingJobRelease).filter(
        st.StreamingJobRelease.approval_status.in_(("pending", "approved"))
    ).all():
        if vids.intersection(
            st._parse_version_id_list(
                getattr(release, "dependency_file_version_ids", None)
            )
        ):
            n += 1
    return n


def _ser_ver(db: Session, ver, username_by_id=None) -> dict:
    umap = username_by_id or {}
    uid = getattr(ver, "uploaded_by", None)
    return {
        "id": ver.id,
        "artifact_id": ver.artifact_id,
        "version": ver.version,
        "file_name": ver.file_name,
        "size_bytes": ver.size_bytes,
        "sha256": ver.sha256,
        "change_note": ver.change_note,
        "status": ver.status,
        "uploaded_by": uid,
        "uploaded_by_username": umap.get(uid) if uid else None,
        "uploaded_at": ver.uploaded_at,
    }


def _ser_connector_art(db: Session, art, *, include_versions: bool = False) -> dict:
    refs = _job_refs_connector_artifact(db, art.id)
    latest = (
        db.query(st.StreamingConnectorVersion)
        .filter(st.StreamingConnectorVersion.artifact_id == art.id)
        .order_by(st.StreamingConnectorVersion.version.desc())
        .first()
    )
    uids = [art.owner_id, art.created_by, latest.uploaded_by if latest else None]
    umap = st._username_map(db, uids)
    out = {
        "id": art.id,
        "workspace_id": art.workspace_id,
        "name": art.name,
        "description": art.description,
        "owner_id": art.owner_id,
        "owner_username": umap.get(art.owner_id) if art.owner_id else None,
        "created_by": art.created_by,
        "created_by_username": umap.get(art.created_by) if art.created_by else None,
        "created_at": art.created_at,
        "updated_at": art.updated_at,
        "ref_job_count": refs,
        "latest_version": _ser_ver(db, latest, umap) if latest else None,
    }
    if include_versions:
        vers = (
            db.query(st.StreamingConnectorVersion)
            .filter(st.StreamingConnectorVersion.artifact_id == art.id)
            .order_by(st.StreamingConnectorVersion.version.desc())
            .all()
        )
        vumap = st._username_map(db, [v.uploaded_by for v in vers])
        out["versions"] = [_ser_ver(db, v, vumap) for v in vers]
    return out


def _ser_file_art(db: Session, art, *, include_versions: bool = False) -> dict:
    refs = _job_refs_file_artifact(db, art.id)
    latest = (
        db.query(st.StreamingFileVersion)
        .filter(st.StreamingFileVersion.artifact_id == art.id)
        .order_by(st.StreamingFileVersion.version.desc())
        .first()
    )
    uids = [art.owner_id, art.created_by, latest.uploaded_by if latest else None]
    umap = st._username_map(db, uids)
    out = {
        "id": art.id,
        "workspace_id": art.workspace_id,
        "name": art.name,
        "description": art.description,
        "owner_id": art.owner_id,
        "owner_username": umap.get(art.owner_id) if art.owner_id else None,
        "created_by": art.created_by,
        "created_by_username": umap.get(art.created_by) if art.created_by else None,
        "created_at": art.created_at,
        "updated_at": art.updated_at,
        "ref_job_count": refs,
        "latest_version": _ser_ver(db, latest, umap) if latest else None,
    }
    if include_versions:
        vers = (
            db.query(st.StreamingFileVersion)
            .filter(st.StreamingFileVersion.artifact_id == art.id)
            .order_by(st.StreamingFileVersion.version.desc())
            .all()
        )
        vumap = st._username_map(db, [v.uploaded_by for v in vers])
        out["versions"] = [_ser_ver(db, v, vumap) for v in vers]
    return out


def register_routes(router: APIRouter) -> None:
    @router.get("/connector-artifacts")
    def list_connector_artifacts(
        workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
        rows = (
            db.query(st.StreamingConnectorArtifact)
            .filter(st.StreamingConnectorArtifact.workspace_id == workspace_id)
            .order_by(st.StreamingConnectorArtifact.updated_at.desc())
            .all()
        )
        return [_ser_connector_art(db, a) for a in rows]

    @router.post("/connector-artifacts")
    def create_connector_artifact(
        body: ArtifactCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="制品名称不能为空")
        art = st.StreamingConnectorArtifact(
            workspace_id=body.workspace_id,
            name=name,
            description=(body.description or "").strip() or None,
            owner_id=current_user.id,
            created_by=current_user.id,
        )
        db.add(art)
        db.commit()
        db.refresh(art)
        return _ser_connector_art(db, art)

    @router.get("/connector-artifacts/{artifact_id}")
    def get_connector_artifact(
        artifact_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        art = db.query(st.StreamingConnectorArtifact).filter(st.StreamingConnectorArtifact.id == artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_READ)
        return _ser_connector_art(db, art, include_versions=True)

    @router.put("/connector-artifacts/{artifact_id}")
    def update_connector_artifact(
        artifact_id: int,
        body: ArtifactUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ):
        art = db.query(st.StreamingConnectorArtifact).filter(st.StreamingConnectorArtifact.id == artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="制品名称不能为空")
            art.name = name
        if body.description is not None:
            art.description = body.description.strip() or None
        art.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(art)
        return _ser_connector_art(db, art, include_versions=True)

    @router.delete("/connector-artifacts/{artifact_id}")
    def delete_connector_artifact(
        artifact_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        art = db.query(st.StreamingConnectorArtifact).filter(st.StreamingConnectorArtifact.id == artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        refs = _job_refs_connector_artifact(db, artifact_id)
        if refs:
            raise HTTPException(status_code=409, detail=f"仍有 {refs} 个作业引用该制品，请先解绑后再删除")
        db.delete(art)
        db.commit()
        return {"message": "删除成功"}

    @router.post("/connector-artifacts/{artifact_id}/versions")
    async def upload_connector_version(
        artifact_id: int,
        file: UploadFile = File(...),
        change_note: Optional[str] = Query(None),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ):
        from app.services.jar_artifact import save_kind_library_bytes

        art = db.query(st.StreamingConnectorArtifact).filter(st.StreamingConnectorArtifact.id == artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        if not file.filename or not file.filename.lower().endswith(".jar"):
            raise HTTPException(status_code=400, detail="连接器只支持 .jar 文件")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="空文件")
        mx = (
            db.query(func.max(st.StreamingConnectorVersion.version))
            .filter(st.StreamingConnectorVersion.artifact_id == artifact_id)
            .scalar()
        )
        next_ver = (int(mx) if mx is not None else 0) + 1
        store_name = JAR_ARTIFACT_FILENAME
        save_kind_library_bytes(
            "connectors",
            art.id,
            next_ver,
            content,
            store_name,
            content_type="application/java-archive",
        )
        ver = st.StreamingConnectorVersion(
            artifact_id=art.id,
            version=next_ver,
            file_name=file.filename,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_key=f"library/connectors/{art.id}/v{next_ver}/{store_name}",
            change_note=(change_note or "").strip() or None,
            status="active",
            uploaded_by=current_user.id,
        )
        db.add(ver)
        art.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(ver)
        return _ser_ver(db, ver, st._username_map(db, [ver.uploaded_by]))

    @router.post("/connector-versions/{version_id}/deprecate")
    def deprecate_connector_version(
        version_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        ver = db.query(st.StreamingConnectorVersion).filter(st.StreamingConnectorVersion.id == version_id).first()
        if not ver:
            raise HTTPException(status_code=404, detail="版本不存在")
        art = db.query(st.StreamingConnectorArtifact).filter(st.StreamingConnectorArtifact.id == ver.artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        refs = _job_refs_connector(db, version_id)
        if refs:
            raise HTTPException(status_code=409, detail=f"仍有 {refs} 个作业绑定该版本，请先改绑后再废弃")
        ver.status = "deprecated"
        art.updated_at = datetime.utcnow()
        db.commit()
        return _ser_ver(db, ver, st._username_map(db, [ver.uploaded_by]))

    @router.get("/connector-versions/{version_id}/artifact.jar")
    def download_connector_version(version_id: int, token: str = Query(...), db: Session = Depends(get_db)):
        from app.services.jar_artifact import artifact_download_token_is_valid, kind_library_file_path

        if not artifact_download_token_is_valid(token):
            raise HTTPException(status_code=403, detail="无效 token")
        ver = db.query(st.StreamingConnectorVersion).filter(st.StreamingConnectorVersion.id == version_id).first()
        if not ver:
            raise HTTPException(status_code=404, detail="版本不存在")
        path = kind_library_file_path("connectors", int(ver.artifact_id), int(ver.version), JAR_ARTIFACT_FILENAME)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(path, media_type="application/java-archive", filename=ver.file_name or "artifact.jar")

    # -------- dependency files --------
    @router.get("/file-artifacts")
    def list_file_artifacts(
        workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
        rows = (
            db.query(st.StreamingFileArtifact)
            .filter(st.StreamingFileArtifact.workspace_id == workspace_id)
            .order_by(st.StreamingFileArtifact.updated_at.desc())
            .all()
        )
        return [_ser_file_art(db, a) for a in rows]

    @router.post("/file-artifacts")
    def create_file_artifact(
        body: ArtifactCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="制品名称不能为空")
        art = st.StreamingFileArtifact(
            workspace_id=body.workspace_id,
            name=name,
            description=(body.description or "").strip() or None,
            owner_id=current_user.id,
            created_by=current_user.id,
        )
        db.add(art)
        db.commit()
        db.refresh(art)
        return _ser_file_art(db, art)

    @router.get("/file-artifacts/{artifact_id}")
    def get_file_artifact(
        artifact_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        art = db.query(st.StreamingFileArtifact).filter(st.StreamingFileArtifact.id == artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_READ)
        return _ser_file_art(db, art, include_versions=True)

    @router.put("/file-artifacts/{artifact_id}")
    def update_file_artifact(
        artifact_id: int,
        body: ArtifactUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ):
        art = db.query(st.StreamingFileArtifact).filter(st.StreamingFileArtifact.id == artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="制品名称不能为空")
            art.name = name
        if body.description is not None:
            art.description = body.description.strip() or None
        art.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(art)
        return _ser_file_art(db, art, include_versions=True)

    @router.delete("/file-artifacts/{artifact_id}")
    def delete_file_artifact(
        artifact_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        art = db.query(st.StreamingFileArtifact).filter(st.StreamingFileArtifact.id == artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        refs = _job_refs_file_artifact(db, artifact_id)
        if refs:
            raise HTTPException(status_code=409, detail=f"仍有 {refs} 个作业引用该制品，请先解绑后再删除")
        db.delete(art)
        db.commit()
        return {"message": "删除成功"}

    @router.post("/file-artifacts/{artifact_id}/versions")
    async def upload_file_version(
        artifact_id: int,
        file: UploadFile = File(...),
        change_note: Optional[str] = Query(None),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ):
        from app.services.jar_artifact import save_kind_library_bytes

        art = db.query(st.StreamingFileArtifact).filter(st.StreamingFileArtifact.id == artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        if not file.filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="空文件")
        mx = (
            db.query(func.max(st.StreamingFileVersion.version))
            .filter(st.StreamingFileVersion.artifact_id == artifact_id)
            .scalar()
        )
        next_ver = (int(mx) if mx is not None else 0) + 1
        store_name = file.filename
        save_kind_library_bytes("files", art.id, next_ver, content, store_name)
        ver = st.StreamingFileVersion(
            artifact_id=art.id,
            version=next_ver,
            file_name=file.filename,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_key=f"library/files/{art.id}/v{next_ver}/{store_name}",
            change_note=(change_note or "").strip() or None,
            status="active",
            uploaded_by=current_user.id,
        )
        db.add(ver)
        art.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(ver)
        return _ser_ver(db, ver, st._username_map(db, [ver.uploaded_by]))

    @router.post("/file-versions/{version_id}/deprecate")
    def deprecate_file_version(
        version_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
    ):
        ver = db.query(st.StreamingFileVersion).filter(st.StreamingFileVersion.id == version_id).first()
        if not ver:
            raise HTTPException(status_code=404, detail="版本不存在")
        art = db.query(st.StreamingFileArtifact).filter(st.StreamingFileArtifact.id == ver.artifact_id).first()
        if not art:
            raise HTTPException(status_code=404, detail="制品不存在")
        assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
        refs = _job_refs_file(db, version_id)
        if refs:
            raise HTTPException(status_code=409, detail=f"仍有 {refs} 个作业绑定该版本，请先改绑后再废弃")
        ver.status = "deprecated"
        art.updated_at = datetime.utcnow()
        db.commit()
        return _ser_ver(db, ver, st._username_map(db, [ver.uploaded_by]))

    @router.get("/file-versions/{version_id}/artifact")
    def download_file_version(version_id: int, token: str = Query(...), db: Session = Depends(get_db)):
        from app.services.jar_artifact import artifact_download_token_is_valid, kind_library_file_path

        if not artifact_download_token_is_valid(token):
            raise HTTPException(status_code=403, detail="无效 token")
        ver = db.query(st.StreamingFileVersion).filter(st.StreamingFileVersion.id == version_id).first()
        if not ver:
            raise HTTPException(status_code=404, detail="版本不存在")
        path = kind_library_file_path("files", int(ver.artifact_id), int(ver.version), ver.file_name)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(path, filename=ver.file_name or "artifact.bin")
