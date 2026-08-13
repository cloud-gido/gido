# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""在 Flink 运行时镜像中启动短生命周期 Job，执行 batch SELECT 预览。"""
from __future__ import annotations

import base64
import html
import json
import logging
import re
import secrets
import time
from typing import Any, Dict, List

from fastapi import HTTPException

from app.core.config import settings
from app.services.flink_operator_submit import _load_k8s_config, kubernetes_api_available
from app.services.flink_pod_scheduling import operator_paimon_warehouse_pod_template, operator_scheduling_pod_template
from app.services.stream_sql_preview_validate import parse_stream_preview_statements

logger = logging.getLogger(__name__)

PREVIEW_MARKER = "GIDO_PREVIEW_JSON:"
_PREVIEW_JOB_PREFIX = "gido-sql-preview-"
_SQL_SET_PATTERN = re.compile(
    r"SET\s+'([^']+)'\s*=\s*'(.*?)'\s*;",
    re.IGNORECASE | re.DOTALL,
)


def _operator_namespace() -> str:
    ns = (settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
    return ns or "flink"


def _preview_image() -> str:
    img = (settings.FLINK_OPERATOR_IMAGE or "").strip()
    if not img:
        raise HTTPException(status_code=503, detail="未配置 FLINK_OPERATOR_IMAGE，无法执行 Stream SQL 预览")
    return img


def _k8s_api_error(exc: Exception) -> HTTPException:
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None) or str(exc)
    body = getattr(exc, "body", None)
    detail = f"Kubernetes API 错误 ({status or 'unknown'}): {reason}"
    if body:
        detail = f"{detail}\n{body[:1500]}"
    code = 403 if status == 403 else 502
    return HTTPException(status_code=code, detail=detail)


def _parse_preview_json(logs: str) -> Dict[str, Any]:
    idx = logs.rfind(PREVIEW_MARKER)
    if idx < 0:
        tail = logs[-2500:] if logs else ""
        raise HTTPException(
            status_code=500,
            detail=(
                "预览作业未输出结果标记；请重新构建并推送 gido-flink-sql-runner 镜像（含 SqlRunner --preview），"
                "并确认 SQL 含 SET batch 与 SELECT。日志尾部：\n" + tail
            ),
        )
    raw = logs[idx + len(PREVIEW_MARKER) :].strip()
    line = raw.splitlines()[0].strip()
    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"预览结果 JSON 解析失败: {e}") from e
    return {
        "columns": data.get("columns") or [],
        "column_types": data.get("column_types") or [],
        "rows": data.get("rows") or [],
        "total": int(data.get("total") or 0),
        "truncated": bool(data.get("truncated")),
    }


def _paimon_volume_mounts() -> tuple[List[dict], List[dict]]:
    wh = (settings.PAIMON_WAREHOUSE_DEFAULT or "").strip().lower()
    if not wh.startswith("file://"):
        return [], []
    tpl = operator_paimon_warehouse_pod_template()
    if not tpl:
        return [], []
    spec = tpl.get("spec") or {}
    return list(spec.get("volumes") or []), list((spec.get("containers") or [{}])[0].get("volumeMounts") or [])


def _preview_uses_irsa(script: str) -> bool:
    if not getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True):
        return False
    # 预览 SQL 可能只写 SELECT 表名，真实 s3a 路径来自 Paimon 表元数据/catalog/默认 warehouse。
    # 因此在 EKS IRSA 模式下，只要没有显式静态 AK/SK，就提前注入 WebIdentity。
    return not _sql_has_static_s3_keys(script)


def _preview_java_sys_props(script: str) -> str:
    """Hadoop/Paimon 读 s3a 时不走 Flink TableConfig，须用 JVM -D 提前注入 fs.s3a.*。"""
    if not _preview_uses_irsa(script):
        return ""
    flags: List[str] = []
    provider = (settings.FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER or "").strip()
    if provider:
        flags.append(f"-Dfs.s3a.aws.credentials.provider={provider}")
    region = (settings.GIDO_ARTIFACT_S3_REGION or "").strip()
    if not region:
        for match in _SQL_SET_PATTERN.finditer(script or ""):
            key = match.group(1).strip()
            if key in ("fs.s3a.endpoint.region", "fs.s3a.region"):
                region = (match.group(2) or "").strip()
                break
    if region:
        flags.append(f"-Dfs.s3a.endpoint.region={region}")
    return " ".join(flags)


def _preview_hadoop_fs_props(script: str) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for match in _SQL_SET_PATTERN.finditer(script or ""):
        key = match.group(1).strip()
        if key.startswith("fs."):
            props[key] = match.group(2)
    return props


def _core_site_xml(props: Dict[str, str]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<configuration>"]
    for key, value in props.items():
        lines.append(
            "  <property><name>"
            + html.escape(key, quote=True)
            + "</name><value>"
            + html.escape(value or "", quote=True)
            + "</value></property>"
        )
    lines.append("</configuration>")
    return "\n".join(lines) + "\n"


def _preview_shell(script: str, limit: int) -> str:
    """将 SQL 以 base64 写入 Pod 内临时文件，避免 ConfigMap 挂载竞态。"""
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    java_opts = _preview_java_sys_props(script)
    java_cmd = f"java {java_opts}" if java_opts else "java"
    hadoop_props = _preview_hadoop_fs_props(script)
    # IRSA 时即便 SQL 未写 SET fs.*，也要把 provider/region 写入 core-site，
    # 供 Hadoop/Paimon 与 JVM -D 一并生效。
    if _preview_uses_irsa(script):
        provider = (settings.FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER or "").strip()
        if provider and "fs.s3a.aws.credentials.provider" not in hadoop_props:
            hadoop_props["fs.s3a.aws.credentials.provider"] = provider
        region = (settings.GIDO_ARTIFACT_S3_REGION or "").strip()
        if not region:
            for key in ("fs.s3a.endpoint.region", "fs.s3a.region"):
                if hadoop_props.get(key):
                    region = hadoop_props[key].strip()
                    break
        if region and "fs.s3a.endpoint.region" not in hadoop_props:
            hadoop_props["fs.s3a.endpoint.region"] = region
    hadoop_conf_bootstrap = ""
    classpath = "/opt/flink/usrlib/sql-runner.jar:/opt/flink/lib/*"
    if hadoop_props:
        core_site = base64.b64encode(_core_site_xml(hadoop_props).encode("utf-8")).decode("ascii")
        hadoop_conf_bootstrap = (
            "mkdir -p /tmp/gido-hadoop-conf\n"
            f"echo '{core_site}' | base64 -d > /tmp/gido-hadoop-conf/core-site.xml\n"
            "export HADOOP_CONF_DIR=/tmp/gido-hadoop-conf\n"
            "echo 'GIDO_PREVIEW_HADOOP_CONF: core-site.xml=set'\n"
        )
        classpath = "/tmp/gido-hadoop-conf:" + classpath
    irsa_debug = ""
    if java_opts:
        irsa_debug = (
            f"echo 'GIDO_PREVIEW_IRSA: java_opts={java_opts}'\n"
            "for n in AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE AWS_REGION AWS_DEFAULT_REGION; do "
            'if [ -n "${!n:-}" ]; then echo "GIDO_PREVIEW_IRSA: $n=set"; '
            'else echo "GIDO_PREVIEW_IRSA: $n=missing"; fi; '
            "done\n"
        )
    return (
        "set -euo pipefail\n"
        f"echo '{encoded}' | base64 -d > /tmp/gido-preview.sql\n"
        f"{hadoop_conf_bootstrap}"
        f"{irsa_debug}"
        f"{java_cmd} -cp '{classpath}' "
        "com.gido.flink.SqlRunner "
        f"file:///tmp/gido-preview.sql --preview {limit}\n"
    )


_IRSA_STRIP_FS_KEYS = frozenset({
    "fs.s3a.access.key",
    "fs.s3a.secret.key",
    "fs.s3a.session.token",
    "fs.s3a.aws.credentials.provider",
})


def _script_uses_s3(script: str) -> bool:
    s = (script or "").lower()
    return "s3a://" in s or "s3://" in s


def _strip_sql_set_keys(script: str, keys: frozenset[str]) -> str:
    def _drop(match: re.Match[str]) -> str:
        if match.group(1).strip() in keys:
            return ""
        return match.group(0)

    return _SQL_SET_PATTERN.sub(_drop, script or "")


def _sql_has_static_s3_keys(script: str) -> bool:
    """仅当 SQL 同时给出非占位的 access.key + secret.key 时视为本地静态凭证。"""
    values: Dict[str, str] = {}
    for match in _SQL_SET_PATTERN.finditer(script or ""):
        key = match.group(1).strip()
        val = (match.group(2) or "").strip()
        if val and not val.startswith("${"):
            values[key] = val
    return bool(values.get("fs.s3a.access.key") and values.get("fs.s3a.secret.key"))


def _prepare_preview_script(sql: str) -> str:
    """EKS IRSA：无静态 AK/SK 时注入 WebIdentity；本地 SQL 显式 AK/SK 时保留不动。"""
    statements = parse_stream_preview_statements(sql)
    script = ";\n".join(statements) + ";\n"
    if not getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True):
        return script
    if _sql_has_static_s3_keys(script):
        return script

    script = _strip_sql_set_keys(script, _IRSA_STRIP_FS_KEYS)
    prepends: List[str] = []
    provider = (settings.FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER or "").strip()
    if provider:
        prepends.append(f"SET 'fs.s3a.aws.credentials.provider' = '{provider}';")
    region = (settings.GIDO_ARTIFACT_S3_REGION or "").strip()
    if region:
        prepends.append(f"SET 'fs.s3a.endpoint.region' = '{region}';")
    if prepends:
        return "\n".join(prepends) + "\n" + script
    return script


def _aws_env_from_sql(script: str) -> List[dict]:
    """预览 Job 是普通 K8s Job，需把 SQL 里的 S3A 静态凭证或 IRSA 区域传给容器环境。"""
    values: Dict[str, str] = {}
    for match in _SQL_SET_PATTERN.finditer(script or ""):
        values[match.group(1).strip()] = match.group(2)

    env: List[dict] = []
    access_key = values.get("fs.s3a.access.key")
    secret_key = values.get("fs.s3a.secret.key")
    session_token = values.get("fs.s3a.session.token")
    region = values.get("fs.s3a.endpoint.region") or values.get("fs.s3a.region")
    endpoint = values.get("fs.s3a.endpoint")
    if not region:
        region = (settings.GIDO_ARTIFACT_S3_REGION or "").strip()
    if not region and endpoint:
        m = re.search(r"s3[.-]([a-z0-9-]+)\.amazonaws\.com", endpoint)
        if m:
            region = m.group(1)
    if access_key and (not getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True) or _sql_has_static_s3_keys(script)):
        env.append({"name": "AWS_ACCESS_KEY_ID", "value": access_key})
    if secret_key and (not getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True) or _sql_has_static_s3_keys(script)):
        env.append({"name": "AWS_SECRET_ACCESS_KEY", "value": secret_key})
    if session_token and (not getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True) or _sql_has_static_s3_keys(script)):
        env.append({"name": "AWS_SESSION_TOKEN", "value": session_token})
    if region:
        env.append({"name": "AWS_DEFAULT_REGION", "value": region})
        if _preview_uses_irsa(script):
            env.append({"name": "AWS_REGION", "value": region})
    return env


def _preview_pod_scheduling_spec() -> Dict[str, Any]:
    """与 FlinkDeployment 共用 nodeSelector/tolerations，避免预览 Job 在污点节点池上 Pending。"""
    tpl = operator_scheduling_pod_template()
    if not tpl:
        return {}
    spec = tpl.get("spec") or {}
    out: Dict[str, Any] = {}
    if spec.get("nodeSelector"):
        out["node_selector"] = dict(spec["nodeSelector"])
    if spec.get("tolerations"):
        out["tolerations"] = list(spec["tolerations"])
    return out


def _wait_job_terminal(batch, core, ns: str, job_name: str, timeout: int) -> tuple[bool, str]:
    from kubernetes.client.rest import ApiException  # type: ignore

    deadline = time.time() + timeout
    pod_name = ""
    failed = True
    while time.time() < deadline:
        try:
            # 用 read_namespaced_job（非 /status 子资源），兼容现有 RBAC jobs:get
            job = batch.read_namespaced_job(job_name, ns)
        except ApiException as e:
            raise _k8s_api_error(e) from e
        st = job.status
        if st and (st.succeeded or 0) >= 1:
            failed = False
            break
        if st and (st.failed or 0) >= 1:
            failed = True
            break
        time.sleep(2)
    else:
        raise HTTPException(status_code=504, detail=f"预览作业超时（{timeout}s）")

    pods = core.list_namespaced_pod(ns, label_selector=f"job-name={job_name}")
    if pods.items:
        pod_name = pods.items[0].metadata.name
    return failed, pod_name


def _cleanup_preview_job(batch, core, ns: str, job_name: str) -> None:
    try:
        batch.delete_namespaced_job(job_name, ns, propagation_policy="Background")
    except Exception:
        logger.debug("delete preview job %s failed", job_name, exc_info=True)


def run_stream_sql_preview(sql: str, *, limit: int = 100) -> Dict[str, Any]:
    if not kubernetes_api_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Stream SQL 预览需要 Kubernetes 访问能力："
                "请将 backend 部署在集群内，或配置 FLINK_K8S_KUBECONFIG_PATH。"
            ),
        )

    script = _prepare_preview_script(sql)
    lim = min(max(int(limit), 1), 10000)
    timeout = int(getattr(settings, "FLINK_STREAM_PREVIEW_TIMEOUT_SEC", 180) or 180)

    from kubernetes import client  # type: ignore
    from kubernetes.client.rest import ApiException  # type: ignore

    _load_k8s_config()
    core = client.CoreV1Api()
    batch = client.BatchV1Api()
    ns = _operator_namespace()
    job_name = f"{_PREVIEW_JOB_PREFIX}{secrets.token_hex(4)}"
    job_created = False

    volume_mounts: List[client.V1VolumeMount] = []
    volumes: List[client.V1Volume] = []
    extra_vols, extra_mounts = _paimon_volume_mounts()
    for m in extra_mounts:
        volume_mounts.append(
            client.V1VolumeMount(
                name=m["name"],
                mount_path=m["mountPath"],
                read_only=m.get("readOnly", False),
            )
        )
    for v in extra_vols:
        pvc = v.get("persistentVolumeClaim") or {}
        volumes.append(
            client.V1Volume(
                name=v["name"],
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=pvc.get("claimName", ""),
                    read_only=pvc.get("readOnly", False),
                ),
            )
        )

    container = client.V1Container(
        name="preview",
        image=_preview_image(),
        image_pull_policy="Always",
        command=["/bin/bash", "-c"],
        args=[_preview_shell(script, lim)],
        env=[client.V1EnvVar(**e) for e in _aws_env_from_sql(script)] or None,
        volume_mounts=volume_mounts or None,
        resources=client.V1ResourceRequirements(
            requests={"cpu": "500m", "memory": "1536Mi"},
            limits={"cpu": "2", "memory": "3Gi"},
        ),
    )
    sched = _preview_pod_scheduling_spec()
    pod_spec = client.V1PodSpec(
        restart_policy="Never",
        automount_service_account_token=True,
        service_account_name=(settings.FLINK_OPERATOR_SERVICE_ACCOUNT or "").strip() or None,
        containers=[container],
        volumes=volumes or None,
        node_selector=sched.get("node_selector"),
        tolerations=[client.V1Toleration(**t) for t in sched.get("tolerations") or []] or None,
    )
    job = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=ns,
            labels={"app": "gido-sql-preview"},
        ),
        spec=client.V1JobSpec(
            ttl_seconds_after_finished=300,
            backoff_limit=0,
            active_deadline_seconds=timeout + 30,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"app": "gido-sql-preview", "job-name": job_name}),
                spec=pod_spec,
            ),
        ),
    )

    try:
        try:
            batch.create_namespaced_job(ns, job)
            job_created = True
        except ApiException as e:
            raise _k8s_api_error(e) from e

        failed, pod_name = _wait_job_terminal(batch, core, ns, job_name, timeout)
        if not pod_name:
            raise HTTPException(status_code=500, detail="预览 Pod 未创建")

        logs = ""
        for _ in range(15):
            try:
                logs = core.read_namespaced_pod_log(pod_name, ns, container="preview")
                break
            except ApiException:
                time.sleep(2)
        if failed:
            diagnostic = "\n".join(
                line
                for line in (logs or "").splitlines()
                if line.startswith("GIDO_PREVIEW_IRSA:") or line.startswith("GIDO_PREVIEW_HADOOP_CONF:")
            )
            tail = (logs or "")[-3000:] or "预览作业失败（无日志）"
            err = f"{diagnostic}\n{tail}" if diagnostic else tail
            raise HTTPException(status_code=400, detail=f"预览 SQL 执行失败：\n{err}")

        parsed = _parse_preview_json(logs or "")
        parsed["error"] = None
        return parsed
    finally:
        if job_created:
            _cleanup_preview_job(batch, core, ns, job_name)
