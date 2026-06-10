# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""
DolphinScheduler API 客户端
负责：项目管理、流程定义同步、触发执行、状态回写
"""
import json
import logging
import re
import requests
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.brand import BRAND_SUITE, DS_PROJECT_DEFAULT
from app.services.datasource_mysql_user import mysql_protocol_connect_user

logger = logging.getLogger(__name__)

# 与 Studio SQL 测试里 _resolve_date_expr 对齐：任意位置出现 $[...] 均视为时间宏，须进 DS globalParams
_DS_TIME_MACRO = re.compile(r"\$\[[^\]]+\]")


def _value_contains_ds_time_macro(v: Any) -> bool:
    if v is None:
        return False
    return bool(_DS_TIME_MACRO.search(str(v).strip()))


def unwrap_ds_numeric(val, *, keys=("id", "code")):
    """
    DolphinScheduler 不同版本 `data` 可能是裸数字，也可能是 {{ id }} / {{ code }}。
    """
    if val is None:
        raise ValueError("DS API 返回 data 为空")
    if isinstance(val, bool):
        raise TypeError(f"unexpected bool in DS numeric: {val}")
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        s = val.strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
        raise ValueError(f"无法解析为整数: {val!r}")
    if isinstance(val, dict):
        for k in keys:
            if k in val and val[k] is not None:
                return unwrap_ds_numeric(val[k], keys=keys)
        if "data" in val and val["data"] is not None:
            return unwrap_ds_numeric(val["data"], keys=keys)
    raise TypeError(f"无法从 DS 响应解析数字: {type(val).__name__} {val!r}")


def _response_json_raw(r: requests.Response) -> dict:
    """Dolphin REST 常为 JSON；非 JSON（反代/HTML）时带出原文便于排障"""
    try:
        return r.json()
    except Exception:
        snippet = r.text.replace("\n", " ")[:4000]
        raise RuntimeError(f"DS 返回非 JSON（HTTP {r.status_code}）: {snippet}") from None


def _format_ds_failure(resp: dict) -> str:
    if not isinstance(resp, dict):
        return repr(resp)
    bits = []
    for k in ("code", "msg", "enMsg", "failed", "success", "requestId"):
        if k in resp and resp[k] not in (None, ""):
            bits.append(f"{k}={resp[k]!r}")
    if "data" in resp:
        bits.append(f"data={resp['data']!r}")
    return "; ".join(bits) if bits else json.dumps(resp, ensure_ascii=False)[:1600]


def _jdbc_other_defaults_for_dolphin(ds_logical_type: str, extra_from_dw: Optional[dict]) -> dict:
    """Dolphin JDBC 连接器常要求 timezone/SSL；空 other 易出现参数构建失败（UI 常为 serverTimezone）。"""
    o: dict[str, Any] = {}
    if isinstance(extra_from_dw, dict):
        o.update(extra_from_dw)
    lt = str(ds_logical_type or "").lower()
    if lt in ("mysql", "doris"):
        o.setdefault("serverTimezone", "Asia/Shanghai")
        o.setdefault("useSSL", "false")
        o.setdefault("allowPublicKeyRetrieval", "true")
    # DS 插件侧通常为字符串 KV
    out: dict[str, Any] = {}
    for k, v in o.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (int, float)):
            out[k] = str(v)
        else:
            out[k] = v
    return out


class DSClient:
    """DolphinScheduler REST API 封装"""

    def __init__(self):
        self.base = settings.DS_URL.rstrip("/")
        self.headers = {"token": (settings.DS_TOKEN or "").strip()}
        self._project_code: Optional[int] = None
        self.project_name = settings.DS_PROJECT_NAME

    def apply_runtime(self, url: str, token: str, project_name: str) -> None:
        """由 ds_runtime.refresh_ds_client 注入库内或环境合并后的配置。"""
        self.base = (url or settings.DS_URL).rstrip("/")
        self.headers = {"token": (token or "").strip()}
        self.project_name = (project_name or settings.DS_PROJECT_NAME or DS_PROJECT_DEFAULT).strip()
        self._project_code = None

    def _get(self, path: str, params: dict = None) -> dict:
        r = requests.get(f"{self.base}{path}", headers=self.headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"DS API error: {data.get('msg')}")
        return data

    def _post(self, path: str, data: dict = None, json: dict = None) -> dict:
        if json is not None:
            r = requests.post(f"{self.base}{path}", headers=self.headers, json=json, timeout=10)
        else:
            # DS 3.x 大部分接口用 query 参数（form-urlencoded）
            r = requests.post(f"{self.base}{path}", headers=self.headers, params=data, timeout=10)
        r.raise_for_status()
        resp = r.json()
        if resp.get("code") != 0:
            raise RuntimeError(f"DS API error: {resp.get('msg')}")
        return resp

    def _put(self, path: str, data: dict = None) -> dict:
        r = requests.put(f"{self.base}{path}", headers=self.headers, params=data, timeout=10)
        r.raise_for_status()
        resp = r.json()
        if resp.get("code") != 0:
            raise RuntimeError(f"DS API error: {resp.get('msg')}")
        return resp

    # ==================== 数据源 ====================

    @staticmethod
    def gido_datasource_canonical_ds_name(gido_ds_id: int) -> str:
        """DS 列表中稳定的名称（与 GIDO 展示名无关，避免改名后重复注册）。"""
        return f"gido_ds_{gido_ds_id}"

    @staticmethod
    def _gido_legacy_ds_name(dw_ds) -> str:
        """历史命名：曾与展示名耦合，仍可被 upsert/delete 认领。"""
        return f"dw_{dw_ds.id}_{dw_ds.name}"

    def _gido_dw_payload_candidates(self, ds, canonical_name: str) -> List[dict]:
        """Dolphin JDBC payload：Doris 使用 MySQL 协议注册优先，再以原生 DORIS 类型兜底。"""
        lt = str(ds.ds_type or "").lower()
        if lt == "mysql":
            dolphin_types = ["MYSQL"]
        elif lt == "postgresql":
            dolphin_types = ["POSTGRESQL"]
        elif lt == "doris":
            # 先试 DORIS 插件类型，再回退 MySQL 协议（与 Dolphin 3.2 数据源中心一致）
            dolphin_types = ["DORIS", "MYSQL"]
        else:
            return []
        note = (f"GIDO 同步 ws={getattr(ds, 'workspace_id', '')} | {ds.name}")[:255]
        port_default = 5432 if lt == "postgresql" else (9030 if lt == "doris" else 3306)
        jdbc_other = _jdbc_other_defaults_for_dolphin(lt, getattr(ds, "extra_config", None))
        db_name = (getattr(ds, "database", None) or "").strip()
        if not db_name and lt == "doris":
            db_name = "default_cluster"

        payloads: List[dict] = []
        for dt in dolphin_types:
            payloads.append(
                {
                    "name": canonical_name,
                    "type": dt,
                    "note": note,
                    "host": ds.host or "",
                    "port": int(ds.port or port_default),
                    "database": db_name if lt == "doris" else (ds.database or ""),
                    "userName": mysql_protocol_connect_user(ds),
                    "password": ds.password or "",
                    "connectType": "",
                    "principal": "",
                    "javaSecurityKrb5Conf": "",
                    "loginUserKeytabUsername": "",
                    "loginUserKeytabPath": "",
                    "other": jdbc_other,
                }
            )
        return payloads

    def _resolve_existing_dolphin_datasource_id(self, ds) -> Optional[int]:
        """按规范名认领；否则按旧名认领（便于沿用历史环境）。"""
        canonical = self.gido_datasource_canonical_ds_name(ds.id)
        for name in (canonical, self._gido_legacy_ds_name(ds)):
            found = self._find_datasource_id(name)
            if found:
                return found
        return None

    def upsert_gido_datasource(self, ds) -> Tuple[Optional[int], Optional[str]]:
        """
        将 GIDO 数据源推送/更新到 DolphinScheduler，返回 (DS 侧 id, JDBC 类型如 MYSQL/DORIS)；
        Hive/Kafka/OSS 等不写 DS 数据源时返回 (None, None)。
        """
        canonical = self.gido_datasource_canonical_ds_name(ds.id)
        payloads = self._gido_dw_payload_candidates(ds, canonical)
        if not payloads:
            return None, None

        existing_id = self._resolve_existing_dolphin_datasource_id(ds)
        # 已存在镜像时仅用首选类型更新，避免在多类型兜底时误改 JDBC 语义
        to_try = [payloads[0]] if existing_id else payloads

        last_failure = ""
        for payload in to_try:
            body = json.dumps(payload, ensure_ascii=False)
            if existing_id:
                r = requests.put(
                    f"{self.base}/datasources/{existing_id}",
                    headers={**self.headers, "Content-Type": "application/json;charset=UTF-8"},
                    data=body.encode("utf-8"),
                    timeout=30,
                )
                r.raise_for_status()
                resp = _response_json_raw(r)
                if resp.get("code") != 0:
                    raise RuntimeError(
                        f"DS 更新数据源失败: {_format_ds_failure(resp)}。请核对 Dolphin UI 同源是否可保存、token 用户是否有数据源编辑权限。"
                    )
                logger.info("DS 数据源已更新 name=%s id=%s type=%s", canonical, existing_id, payload.get("type"))
                return existing_id, str(payload.get("type") or payloads[0].get("type") or "")

            try:
                r = requests.post(
                    f"{self.base}/datasources",
                    headers={**self.headers, "Content-Type": "application/json;charset=UTF-8"},
                    data=body.encode("utf-8"),
                    timeout=30,
                )
                r.raise_for_status()
                resp = _response_json_raw(r)
            except requests.RequestException as e:
                raise RuntimeError(f"DS 创建数据源网络/HTTP失败: {e}") from e

            if resp.get("code") == 0:
                new_id = unwrap_ds_numeric(resp["data"])
                logger.info("DS 数据源已创建: %s id=%s type=%s", canonical, new_id, payload.get("type"))
                return new_id, str(payload.get("type") or "")

            detail = _format_ds_failure(resp)
            last_failure = f"type={payload.get('type')}: {detail}"
            logger.warning("DS create datasource rejected %s", last_failure)

        raise RuntimeError(
            f"DS 创建数据源失败（已尝试 {len(to_try)} 种类型映射）: {last_failure}。"
            f"请先确认：(1) token 用户对「数据源」有创建权限；(2) 名称「{canonical}」在 Dolphin 中不存在重复；"
            f"(3) 在 Dolphin 管理端手工创建同库类型成功（验证 JDBC 参数）；"
            f"(4) 查看 Dolphin api-server 日志中的具体异常。"
        )

    def delete_gido_datasource_mirror(self, ds) -> None:
        """GIDO 删除前/后：尝试删除 Dolphin 侧镜像（规范名与旧命名各删一次，容错历史重复）。"""
        canonical = self.gido_datasource_canonical_ds_name(ds.id)
        for name in (canonical, self._gido_legacy_ds_name(ds)):
            did = self._find_datasource_id(name)
            if did is None:
                continue
            r = requests.delete(f"{self.base}/datasources/{did}", headers=self.headers, timeout=15)
            r.raise_for_status()
            resp = r.json()
            if resp.get("code") != 0:
                raise RuntimeError(f"DS 删除数据源失败: {resp.get('msg')}")
            logger.info("DS 数据源已删除 name=%s id=%s", name, did)

    def sync_datasource(self, ds) -> Tuple[Optional[int], Optional[str]]:
        """兼容 workflow 同步路径：等价于 upsert（旧名仍存在）。"""
        return self.upsert_gido_datasource(ds)

    def _find_datasource_id(self, name: str) -> Optional[int]:
        try:
            resp = self._get("/datasources", params={"pageNo": 1, "pageSize": 100, "searchVal": name})
            for item in resp.get("data", {}).get("totalList", []):
                if item.get("name") == name:
                    x = item.get("id")
                    return int(x) if x is not None else None
        except Exception:
            logger.debug("查找 DS 数据源 name=%s 失败", name, exc_info=True)
        return None

    # ==================== 项目 ====================

    def get_or_create_project(self) -> int:
        """获取或创建 GIDO 对应的 DS 项目，返回 projectCode"""
        if self._project_code:
            return self._project_code
        pname = self.project_name
        resp = self._get("/projects", params={"pageSize": 100, "pageNo": 1, "searchVal": pname})
        for item in resp.get("data", {}).get("totalList", []):
            if item["name"] == pname:
                self._project_code = item["code"]
                return self._project_code
        # 不存在则创建
        resp = self._post("/projects", data={"projectName": pname, "description": f"{BRAND_SUITE} managed"})
        self._project_code = unwrap_ds_numeric(resp.get("data"), keys=("code", "id"))
        logger.info(f"DS 项目已创建: {pname} code={self._project_code}")
        return self._project_code

    # ==================== 流程定义 ====================

    def sync_workflow(self, workflow, db=None) -> Tuple[int, List[Dict[str, Any]]]:
        """
        将 GIDO Workflow 同步为 DS Process Definition
        workflow.dag_config: {nodes: [{node_id, name, node_type, script_content}], edges: [{source, target}]}
        返回 (processDefinitionCode, 每节点同步结果诊断)
        """
        sync_diag: List[Dict[str, Any]] = []
        project_code = self.get_or_create_project()
        dag = workflow.dag_config or {}
        dw_nodes = dag.get("nodes", [])
        edges = dag.get("edges", [])

        # 构建 DS taskDefinitionJson
        task_defs = []
        task_relations = []
        code_map = {}  # node_id -> ds_task_code

        # 从 DS 获取合法的 task code（雪花算法生成）
        num_nodes = len(dw_nodes)
        if num_nodes == 0:
            raise ValueError("工作流没有节点")
        codes_resp = self._get(
            f"/projects/{project_code}/task-definition/gen-task-codes",
            params={"genNum": num_nodes}
        )
        ds_codes_raw = codes_resp.get("data", [])
        ds_codes = [unwrap_ds_numeric(c, keys=("code", "id")) for c in ds_codes_raw]
        if len(ds_codes) < num_nodes:
            raise RuntimeError("DS 生成 task code 数量不足")

        for i, n in enumerate(dw_nodes):
            task_code = ds_codes[i]
            code_map[n["node_id"]] = task_code
            script = n.get("script_content") or ""
            node_type = n.get("node_type", "SHELL")
            datasource_id = n.get("datasource_id")
            diag_row: Dict[str, Any] = {
                "node_id": n.get("node_id"),
                "node_type": node_type,
                "datasource_id": datasource_id,
                "ds_task_type": "SHELL",
                "reason": None,
            }

            if node_type == "SQL" and datasource_id:
                from app.models.workspace import DataSource
                ds_obj = db.query(DataSource).filter(DataSource.id == datasource_id).first() if db else None
                if not ds_obj:
                    diag_row["reason"] = f"数据源 id={datasource_id} 不存在"
                    logger.warning(
                        "SQL 节点数据源不存在，降级为 SHELL: workflow_id=%s node_id=%s ds_id=%s",
                        getattr(workflow, "id", "?"),
                        n.get("node_id"),
                        datasource_id,
                    )
                elif ds_obj:
                    try:
                        ds_ds_id, ds_jdbc_registered = self.sync_datasource(ds_obj)
                    except Exception as e:
                        diag_row["reason"] = f"同步 Dolphin 数据源失败: {e}"
                        logger.warning(
                            "数据源同步到 Dolphin 失败，SQL 节点将降级为 SHELL（workflow_id=%s node_id=%s dw_ds=%s）: %s",
                            getattr(workflow, "id", "?"),
                            n.get("node_id"),
                            datasource_id,
                            e,
                        )
                        ds_ds_id = None
                        ds_jdbc_registered = None

                    if ds_ds_id:
                        import re as _re
                        ds_type_map = {"mysql": "MYSQL", "postgresql": "POSTGRESQL", "doris": "DORIS", "hive": "HIVE"}
                        ds_db_type = (ds_jdbc_registered or "").strip() or ds_type_map.get(
                            ds_obj.ds_type.lower(), "MYSQL"
                        )
                        is_query = bool(_re.match(r'\s*(SELECT|WITH|SHOW|DESC|EXPLAIN)', script.strip(), _re.IGNORECASE))
                        sql_type = 0 if is_query else 1
                        rewritten_sql = _rewrite_sql_builtins(script)
                        node_params = n.get("params") or {}
                        # Dolphin 3.2.x SqlParameters 反序列化时期望 varPool 等字段存在，避免 Worker 侧 NPE
                        ds_id_int = unwrap_ds_numeric(ds_ds_id)
                        logger.info(
                            "DS SQL 节点绑定: workflow_id=%s node_id=%s dw_datasource_id=%s -> dolphin_datasource_id=%s type=%s",
                            getattr(workflow, "id", "?"),
                            n.get("node_id"),
                            datasource_id,
                            ds_id_int,
                            ds_db_type,
                        )
                        task_defs.append({
                            "code": task_code,
                            "name": n.get("name", f"node_{n['node_id']}"),
                            "description": f"SQL | {ds_obj.ds_type}://{ds_obj.host}/{ds_obj.database}",
                            "taskType": "SQL",
                            "isCache": "NO",
                            "taskParams": {
                                "type": ds_db_type,
                                "datasource": ds_id_int,
                                "sql": rewritten_sql,
                                "sqlType": sql_type,
                                "sendEmail": False,
                                "displayRows": 10,
                                "udfs": "",
                                "showType": "TABLE",
                                "connParams": "",
                                "preStatements": [],
                                "postStatements": [],
                                "groupId": 0,
                                "title": "",
                                "limit": 100,
                                "localParams": _build_ds_local_params(node_params),
                                "varPool": [],
                                "resourceList": [],
                            },
                            "flag": "YES",
                            "taskPriority": "MEDIUM",
                            "workerGroup": "default",
                            "environmentCode": -1,
                            "failRetryTimes": n.get("retry_times", 0),
                            "failRetryInterval": 1,
                            "timeoutFlag": "CLOSE",
                            "timeout": n.get("timeout_seconds", 3600) // 60,
                            "delayTime": 0,
                            "cpuQuota": -1,
                            "memoryMax": -1,
                            "taskExecuteType": "BATCH",
                        })
                        diag_row["ds_task_type"] = "SQL"
                        diag_row["dolphin_datasource_id"] = ds_id_int
                        diag_row["jdbc_type"] = ds_db_type
                        sync_diag.append(diag_row)
                        continue

            # PYTHON / SHELL / SQL 无可用数据源（未配置默认源或同步 DS 数据源失败）→ SHELL
            if node_type == "SQL" and not datasource_id and not diag_row.get("reason"):
                diag_row["reason"] = "未解析到数据源（请在节点配置或空间设置指定默认/数仓数据源）"
                logger.warning(
                    "SQL 节点未解析到数据源，降级为 SHELL: workflow_id=%s node_id=%s",
                    getattr(workflow, "id", "?"),
                    n.get("node_id"),
                )
            elif node_type == "SQL" and diag_row.get("reason") is None:
                diag_row["reason"] = "未能注册 Dolphin SQL 任务（见上文数据源同步日志）"
            if node_type == "SQL":
                from app.core.config import settings as _s
                raw_script = (
                    f"curl -s -f -X POST http://gido-backend:8001/api/studio/nodes/{n['node_id']}/run "
                    f"-H 'Content-Type: application/json' "
                    f"-H 'Authorization: Bearer {_s.INTERNAL_TOKEN}' || exit 1"
                )
            elif node_type == "PYTHON":
                raw_script = script or "echo done"
            elif node_type == "SYNC":
                from app.core.config import settings as _s
                node_params = n.get("params") or {}
                sync_tid = node_params.get("sync_task_id") or ""
                raw_script = (
                    f"curl -s -f -X POST http://gido-backend:8001/api/integration/internal/tasks/{sync_tid}/run "
                    f"-H 'Content-Type: application/json' "
                    f"-H 'Authorization: Bearer {_s.INTERNAL_TOKEN}' || exit 1"
                )
            else:
                raw_script = script or "echo done"

            task_defs.append({
                "code": task_code,
                "name": n.get("name", f"node_{n['node_id']}"),
                "description": "",
                "taskType": "SHELL",
                "isCache": "NO",
                "taskParams": {
                    "rawScript": raw_script,
                    "localParams": [],
                    "resourceList": [],
                },
                "flag": "YES",
                "taskPriority": "MEDIUM",
                "workerGroup": "default",
                "environmentCode": -1,
                "failRetryTimes": n.get("retry_times", 0),
                "failRetryInterval": 1,
                "timeoutFlag": "CLOSE",
                "timeout": n.get("timeout_seconds", 3600) // 60,
                "delayTime": 0,
                "cpuQuota": -1,
                "memoryMax": -1,
                "taskExecuteType": "BATCH",
            })
            if node_type != "SQL" or diag_row.get("ds_task_type") != "SQL":
                diag_row["ds_task_type"] = "SHELL" if node_type != "SQL" else diag_row.get("ds_task_type", "SHELL")
            sync_diag.append(diag_row)

        # 构建依赖关系
        has_pre = set()
        for e in edges:
            src_code = code_map.get(e["source"])
            tgt_code = code_map.get(e["target"])
            if src_code and tgt_code:
                task_relations.append({
                    "preTaskCode": src_code,
                    "postTaskCode": tgt_code,
                    "name": "",
                    "preTaskVersion": 1,
                    "postTaskVersion": 1,
                    "conditionType": "NONE",
                    "conditionParams": "{}"
                })
                has_pre.add(tgt_code)

        # 没有前置依赖的节点加起始关系
        for n in dw_nodes:
            tc = code_map[n["node_id"]]
            if tc not in has_pre:
                task_relations.append({
                    "preTaskCode": 0,
                    "postTaskCode": tc,
                    "name": "",
                    "preTaskVersion": 0,
                    "postTaskVersion": 1,
                    "conditionType": "NONE",
                    "conditionParams": "{}"
                })

        import json
        # 收集所有节点里含 $[...] 的参数合并到 globalParams（须 startswith 会漏掉 "xx:$[yyyy-MM-dd-1]" 等）
        all_global_params: dict = {}
        for n in dw_nodes:
            raw_params = n.get("params") or {}
            if not isinstance(raw_params, dict):
                continue
            for k, v in raw_params.items():
                if _value_contains_ds_time_macro(v):
                    all_global_params[k] = str(v).strip()
        payload = {
            "name": f"dw_{workflow.id}_{workflow.name}",
            "description": workflow.description or "",
            "globalParams": _build_ds_global_params(all_global_params),
            "locations": "[]",
            "timeout": 0,
            "taskDefinitionJson": json.dumps(task_defs, ensure_ascii=False),
            "taskRelationJson": json.dumps(task_relations, ensure_ascii=False),
            "executionType": "PARALLEL",
        }

        # 检查是否已存在（按名称）
        existing_code = self._find_process_code(project_code, payload["name"])
        if existing_code:
            # 已存在：先下线，再更新
            try:
                self._post(
                    f"/projects/{project_code}/process-definition/{existing_code}/release",
                    data={"releaseState": "OFFLINE"}
                )
            except Exception:
                pass  # 已经是 OFFLINE 则忽略
            self._put(f"/projects/{project_code}/process-definition/{existing_code}", data=payload)
            logger.info(f"DS 流程定义已更新: {payload['name']} code={existing_code}")
            return existing_code, sync_diag
        else:
            resp = self._post(f"/projects/{project_code}/process-definition", data=payload)
            code = unwrap_ds_numeric(resp.get("data"), keys=("code", "id"))
            logger.info(f"DS 流程定义已创建: {payload['name']} code={code}")
            return code, sync_diag

    def _find_process_code(self, project_code: int, name: str) -> Optional[int]:
        try:
            resp = self._get(f"/projects/{project_code}/process-definition",
                             params={"pageSize": 100, "pageNo": 1, "searchVal": name})
            for item in resp.get("data", {}).get("totalList", []):
                if item["name"] == name:
                    return item["code"]
        except Exception:
            pass
        return None

    # ==================== 执行 ====================

    def run_process(self, project_code: int, process_code: int,
                    business_date: str = None, cron_expr: str = None) -> int:
        """触发一次流程执行，返回 DS processInstanceId"""
        from datetime import datetime
        biz = business_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resp = self._post(f"/projects/{project_code}/executors/start-process-instance", data={
            "processDefinitionCode": process_code,
            "scheduleTime": biz,
            "failureStrategy": "CONTINUE",
            "warningType": "NONE",
            "warningGroupId": 0,
            "execType": "START_PROCESS",
            "startNodeList": "",
            "taskDependType": "TASK_POST",
            "runMode": "RUN_MODE_SERIAL",
            "processInstancePriority": "MEDIUM",
            "workerGroup": "default",
            "environmentCode": -1,
            "startParams": None,
            "expectedParallelismNumber": None,
            "dryRun": 0,
        })
        instance_id = unwrap_ds_numeric(resp["data"], keys=("id", "code"))
        logger.info(f"DS 流程实例已触发: processCode={process_code} instanceId={instance_id}")
        return instance_id

    def get_instance_status(self, project_code: int, instance_id: int) -> dict:
        """查询 DS 流程实例状态（含 commandType，供运维区分定时调度与手动触发）。"""
        resp = self._get(f"/projects/{project_code}/process-instances/{instance_id}")
        data = resp.get("data", {}) or {}
        ct = data.get("commandType") or data.get("command_type")
        raw_state = data.get("state")
        if raw_state is None or raw_state == "":
            for key in (
                "executionStatus",
                "execution_status",
                "processInstanceState",
                "process_instance_state",
                "workflowExecutionStatus",
            ):
                v = data.get(key)
                if v is not None and str(v).strip() != "":
                    raw_state = v
                    break
        dw_status = map_dolphin_process_instance_state(raw_state)
        return {
            "state": raw_state,
            "state_dw": dw_status,
            "startTime": data.get("startTime"),
            "endTime": data.get("endTime"),
            "command_type": ct,
        }

    def list_process_instances(
        self,
        project_code: int,
        *,
        process_definition_code: Optional[int] = None,
        page_no: int = 1,
        page_size: int = 100,
    ) -> List[dict]:
        """
        分页查询流程实例列表（含 Dolphin 定时调度产生、未经过 GIDO /run 的实例）。
        DS 3.x 列表接口按流程定义过滤的参数名为 **processDefineCode**（不是 processDefinitionCode）。
        Dolphin 3.2.x 默认按 start_time desc 排序。
        """
        params: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
        if process_definition_code is not None:
            params["processDefineCode"] = int(process_definition_code)
        resp = self._get(f"/projects/{project_code}/process-instances", params=params)
        data = resp.get("data")
        if isinstance(data, dict):
            return list(data.get("totalList") or [])
        return []

    def list_task_instances(
        self,
        project_code: int,
        process_instance_id: int,
        *,
        page_no: int = 1,
        page_size: int = 100,
    ) -> List[dict]:
        """某流程实例下的任务实例列表（用于回填运维中心节点级明细）。"""
        params = {
            "pageNo": page_no,
            "pageSize": page_size,
            "processInstanceId": int(process_instance_id),
        }
        resp = self._get(f"/projects/{project_code}/task-instances", params=params)
        data = resp.get("data")
        if isinstance(data, dict):
            return list(data.get("totalList") or [])
        return []

    def list_task_instances_all(self, project_code: int, process_instance_id: int, page_size: int = 100) -> List[dict]:
        """拉取该流程实例下全部任务实例（多页拼接，上限约 2000 条）。"""
        out: List[dict] = []
        page = 1
        while page <= 20:
            chunk = self.list_task_instances(
                project_code, process_instance_id, page_no=page, page_size=page_size
            )
            if not chunk:
                break
            out.extend(chunk)
            if len(chunk) < page_size:
                break
            page += 1
        return out

    def set_schedule(self, project_code: int, process_code: int, cron_expr: str):
        """为流程定义设置 Cron 调度，自动将5位 Linux cron 转为 DS 的 Quartz 6位格式"""
        # DS 用 Quartz cron（秒 分 时 日 月 周），标准 Linux cron 是5位（分 时 日 月 周）
        # 自动补秒字段
        parts = cron_expr.strip().split()
        if len(parts) == 5:
            # Linux cron: 分 时 日 月 周
            minute, hour, day, month, week = parts
            # Quartz: 秒 分 时 日 月 周
            # 日和周不能同时为 *，其中一个用 ?
            if day == '*' and week == '*':
                quartz_cron = f'0 {minute} {hour} * {month} ?'
            elif day != '*' and week == '*':
                quartz_cron = f'0 {minute} {hour} {day} {month} ?'
            elif day == '*' and week != '*':
                quartz_cron = f'0 {minute} {hour} ? {month} {week}'
            else:
                quartz_cron = f'0 {minute} {hour} {day} {month} ?'
        elif len(parts) == 6:
            quartz_cron = cron_expr
        else:
            quartz_cron = cron_expr
        # 先查是否已有调度
        resp = self._get(f"/projects/{project_code}/schedules",
                         params={"processDefinitionCode": process_code, "pageSize": 10, "pageNo": 1})
        schedules = resp.get("data", {}).get("totalList", [])
        payload = {
            "processDefinitionCode": process_code,
            "schedule": f'{{"startTime":"2020-01-01 00:00:00","endTime":"2099-12-31 00:00:00","crontab":"{quartz_cron}","timezoneId":"Asia/Shanghai"}}',
            "failureStrategy": "CONTINUE",
            "warningType": "NONE",
            "warningGroupId": 0,
            "processInstancePriority": "MEDIUM",
            "workerGroup": "default",
            "environmentCode": -1,
        }
        if schedules:
            schedule_id = unwrap_ds_numeric(schedules[0].get("id"), keys=("id", "code"))
            try:
                self._post(f"/projects/{project_code}/schedules/{schedule_id}/offline")
            except Exception:
                pass
            self._put(f"/projects/{project_code}/schedules/{schedule_id}", data=payload)
            self._post(f"/projects/{project_code}/schedules/{schedule_id}/online")
        else:
            resp = self._post(f"/projects/{project_code}/schedules", data=payload)
            schedule_id = unwrap_ds_numeric(resp["data"], keys=("id", "code"))
            self._post(f"/projects/{project_code}/schedules/{schedule_id}/online")
        logger.info(f"DS 调度已设置: processCode={process_code} cron={quartz_cron}")

    def online_process(self, project_code: int, process_code: int):
        """上线流程定义（必须上线才能执行）"""
        self._post(
            f"/projects/{project_code}/process-definition/{process_code}/release",
            data={"releaseState": "ONLINE"}
        )

    def delete_process_definition(self, project_code: int, process_code: int) -> None:
        """删除 DS 流程定义（先下线流程与关联调度，再 DELETE）。"""
        pcode = int(project_code)
        code = int(process_code)
        try:
            self._post(
                f"/projects/{pcode}/process-definition/{code}/release",
                data={"releaseState": "OFFLINE"},
            )
        except Exception:
            logger.debug("DS 流程下线（删除前）可忽略", exc_info=True)
        try:
            resp = self._get(
                f"/projects/{pcode}/schedules",
                params={"processDefinitionCode": code, "pageSize": 50, "pageNo": 1},
            )
            for item in resp.get("data", {}).get("totalList", []) or []:
                sid = unwrap_ds_numeric(item.get("id"), keys=("id", "code"))
                try:
                    self._post(f"/projects/{pcode}/schedules/{sid}/offline")
                except Exception:
                    pass
                try:
                    r = requests.delete(
                        f"{self.base}/projects/{pcode}/schedules/{sid}",
                        headers=self.headers,
                        timeout=15,
                    )
                    r.raise_for_status()
                    body = r.json()
                    if body.get("code") != 0:
                        logger.warning("DS 删除调度 sid=%s: %s", sid, _format_ds_failure(body))
                except Exception as e:
                    logger.warning("DS 删除调度 sid=%s 失败: %s", sid, e)
        except Exception as e:
            logger.warning("DS 列举/删除调度失败 processCode=%s: %s", code, e)
        r = requests.delete(
            f"{self.base}/projects/{pcode}/process-definition/{code}",
            headers=self.headers,
            timeout=30,
        )
        r.raise_for_status()
        body = _response_json_raw(r)
        if body.get("code") != 0:
            raise RuntimeError(f"DS 删除流程定义失败: {_format_ds_failure(body)}")
        logger.info("DS 流程定义已删除 project=%s processCode=%s", pcode, code)


def _build_ds_local_params(params: dict) -> list:
    """将节点参数字典转为 DS localParams 格式
    $[...] 表达式在 DS localParams 里不会被解析，需要放到 globalParams
    这里只返回静态字符串值的参数
    """
    result = []
    for k, v in (params or {}).items():
        str_v = str(v).strip()
        if _value_contains_ds_time_macro(str_v):
            continue
        result.append({"prop": k, "direct": "IN", "type": "VARCHAR", "value": str_v})
    return result


def _build_ds_global_params(params: dict) -> str:
    """将含 $[...] 的时间宏参数转为 DS globalParams JSON 字符串（整段原样作为 value，与 Studio 替换逻辑一致）"""
    import json
    result = []
    for k, v in (params or {}).items():
        str_v = str(v).strip()
        if _value_contains_ds_time_macro(str_v):
            result.append({"prop": k, "direct": "IN", "type": "VARCHAR", "value": str_v})
    return json.dumps(result, ensure_ascii=False) if result else "[]"


def _rewrite_sql_builtins(sql: str) -> str:
    """将 GIDO 内置变量映射为 DS 内置变量
    ${bizdate}   -> ${system.biz.date}   (yyyyMMdd 格式的业务日期前一天)
    ${yesterday} -> ${system.biz.date}
    """
    import re
    sql = re.sub(r'\$\{bizdate\}', '${system.biz.date}', sql)
    sql = re.sub(r'\$\{yesterday\}', '${system.biz.date}', sql)
    return sql


def dolphin_process_instance_console_url(
    project_code: int,
    process_instance_id: int,
    db: Optional[Session] = None,
    workspace_id: Optional[int] = None,
) -> str:
    """DolphinScheduler 3.x：工作流实例详情页（与 UI 路由 workflow/instances/:id 对齐）。"""
    from app.core.config import settings

    if db is not None:
        from app.services.ds_runtime import get_dolphin_runtime

        cfg = get_dolphin_runtime(db, workspace_id)
        ui = (cfg.ui_url or "").strip()
        api_root = (cfg.url or "").strip()
    else:
        ui = (settings.DS_UI_URL or "").strip()
        api_root = (settings.DS_URL or "").strip()
    if not ui:
        ui = api_root.rstrip("/") + "/ui"
    return f"{ui.rstrip('/')}/#/projects/{int(project_code)}/workflow/instances/{int(process_instance_id)}"


def dolphin_workflow_console_url(
    project_code: int,
    process_search_name: str = "",
    db: Optional[Session] = None,
    workspace_id: Optional[int] = None,
) -> str:
    """DolphinScheduler 3.x：工作流定义列表页，可用 searchVal 定位流程名（如 dw_{id}_{name}）。"""
    from urllib.parse import quote
    from app.core.config import settings

    if db is not None:
        from app.services.ds_runtime import get_dolphin_runtime

        cfg = get_dolphin_runtime(db, workspace_id)
        ui = (cfg.ui_url or "").strip()
        api_root = (cfg.url or "").strip()
    else:
        ui = (settings.DS_UI_URL or "").strip()
        api_root = (settings.DS_URL or "").strip()
    if not ui:
        ui = api_root.rstrip("/") + "/ui"
    base = f"{ui.rstrip('/')}/#/projects/{project_code}/workflow-definition/list"
    if process_search_name:
        return f"{base}?searchVal={quote(process_search_name)}"
    return base


# 单例
ds_client = DSClient()


# DS 状态 -> GIDO 状态映射（字符串枚举，见 WorkflowExecutionStatus 等）
DS_STATE_MAP = {
    "SUCCESS": "success",
    "FAILURE": "failed",
    "FAILED": "failed",
    "RUNNING_EXECUTION": "running",
    "SUBMITTED_SUCCESS": "running",
    "DISPATCH": "running",
    "PAUSE": "running",
    "STOP": "killed",
    "KILL": "killed",
    "KILLED": "killed",
    "WAITING_THREAD": "running",
    "WAITING_DEPEND": "running",
    "DELAY_EXECUTION": "pending",
    "FORCED_SUCCESS": "success",
    "SERIAL_WAIT": "pending",
}

# Dolphin 3.x 流程实例 API 可能返回 WorkflowExecutionStatus 的**数值码**而非字符串（此时 DS_STATE_MAP 无法命中）
DS_PROCESS_INSTANCE_CODE_TO_DW = {
    0: "running",   # SUBMITTED_SUCCESS
    1: "running",   # RUNNING_EXECUTION
    2: "running",
    3: "running",   # PAUSE
    4: "running",
    5: "killed",    # STOP
    6: "failed",    # FAILURE
    7: "success",   # SUCCESS
    8: "running",   # NEED_FAULT_TOLERANCE
    9: "killed",    # KILL
    10: "running",
    11: "running",
    12: "pending",  # DELAY_EXECUTION
    13: "success",  # FORCED_SUCCESS
    14: "pending",  # SERIAL_WAIT
    15: "running",
    16: "running",
    17: "running",  # DISPATCH
    18: "running",
}


def map_dolphin_process_instance_state(raw: Any) -> str:
    """
    将 Dolphin 流程实例 state / executionStatus（字符串或数值）映射为 GIDO 的 status 语义。
    无法识别时保守为 running，避免误把成功标成失败。
    """
    if raw is None or raw == "":
        return "running"
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        try:
            raw = int(raw.strip())
        except ValueError:
            pass
    if isinstance(raw, (int, float)):
        return DS_PROCESS_INSTANCE_CODE_TO_DW.get(int(raw), "running")
    s = str(raw).strip().upper()
    if s in DS_STATE_MAP:
        return DS_STATE_MAP[s]
    if "SUCCESS" in s and "FAIL" not in s:
        return "success"
    if "FAIL" in s:
        return "failed"
    if "KILL" in s or s == "STOP":
        return "killed"
    if "RUNNING" in s or "DISPATCH" in s or "SUBMITTED" in s or "WAIT" in s:
        return "running"
    return "running"
