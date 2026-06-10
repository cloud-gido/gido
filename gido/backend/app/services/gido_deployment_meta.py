# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""FlinkDeployment 命名与 metadata annotations（多租户 + 版本追踪）。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _sanitize_k8s_name_part(value: str) -> str:
    s = re.sub(r"[^a-z0-9\-]", "-", str(value or "").lower()).strip("-")
    return s or "0"


def _truncate_k8s_name(name: str, max_len: int = 63) -> str:
    name = (name or "").strip("-")
    if len(name) <= max_len:
        return name or "gido"
    return name[:max_len].rstrip("-")


def jar_deployment_name(workspace_id: int, job_id: int) -> str:
    raw = f"gido-jar-{_sanitize_k8s_name_part(workspace_id)}-{_sanitize_k8s_name_part(job_id)}"
    return _truncate_k8s_name(raw)


def sql_deployment_name(workspace_id: int, job_id: int) -> str:
    raw = f"gido-sql-{_sanitize_k8s_name_part(workspace_id)}-{_sanitize_k8s_name_part(job_id)}"
    return _truncate_k8s_name(raw)


def sql_configmap_name(workspace_id: int, job_id: int) -> str:
    raw = f"gido-sql-script-{_sanitize_k8s_name_part(workspace_id)}-{_sanitize_k8s_name_part(job_id)}"
    return _truncate_k8s_name(raw)


def sql_script_hash(content: str) -> str:
    data = (content or "").encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class GidoDeploymentMeta:
    workspace_id: int
    job_id: int
    job_type: str
    sql_version: Optional[str] = None
    sql_hash: Optional[str] = None
    submitted_by: Optional[str] = None
    submitted_at: Optional[str] = None

    def labels(self) -> Dict[str, str]:
        jt = (self.job_type or "jar").strip().lower()
        return {
            "app.kubernetes.io/managed-by": "gido",
            "gido.io/job-type": jt,
            "gido.io/workspace-id": str(int(self.workspace_id)),
            "gido.io/job-id": str(int(self.job_id)),
        }

    def annotations(self) -> Dict[str, str]:
        ann: Dict[str, str] = {
            "gido.io/workspace-id": str(int(self.workspace_id)),
            "gido.io/job-id": str(int(self.job_id)),
            "gido.io/job-type": (self.job_type or "").strip().lower(),
        }
        if self.sql_version:
            if (self.job_type or "").lower() == "sql":
                ann["gido.io/sql-version"] = str(self.sql_version)
            else:
                ann["gido.io/submit-version"] = str(self.sql_version)
        if self.sql_hash:
            ann["gido.io/sql-hash"] = str(self.sql_hash)
        if self.submitted_by:
            ann["gido.io/submitted-by"] = str(self.submitted_by)
        if self.submitted_at:
            ann["gido.io/submitted-at"] = str(self.submitted_at)
        return ann

    def apply_to_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        meta = body.setdefault("metadata", {})
        labels = dict(meta.get("labels") or {})
        labels.update(self.labels())
        meta["labels"] = labels
        annotations = dict(meta.get("annotations") or {})
        annotations.update(self.annotations())
        meta["annotations"] = annotations
        return body


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
