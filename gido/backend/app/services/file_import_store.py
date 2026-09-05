# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""本地文件导入：上传文件流式落盘与元数据。

多副本（EKS replicas>1）时本地 emptyDir 不共享：有制品 S3 前缀则 meta/分片走 S3，
已收分片集合优先 Redis，保证任意 Pod 可续传与合并。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services.artifact_s3 import (
    artifact_s3_enabled,
    build_shared_object_uri,
    delete_shared_object,
    delete_shared_objects_with_prefix,
    download_shared_object_to_file,
    get_shared_object,
    list_shared_object_names,
    put_shared_object,
    put_shared_object_file,
)
from app.services import shared_state

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\u4e00-\u9fff-]+")
_SESSION_TTL_SEC = 7 * 24 * 3600


def _root() -> Path:
    d = Path(settings.FILE_IMPORT_UPLOAD_DIR).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_import_shared_enabled() -> bool:
    """生产多副本依赖制品 S3；本地/单副本无 S3 时仍用磁盘。"""
    return artifact_s3_enabled()


def sanitize_filename(name: str) -> str:
    base = Path(name or "upload").name
    base = _SAFE_NAME.sub("_", base).strip("._") or "upload"
    return base[:180]


def detect_format(filename: str) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".xlsx") or lower.endswith(".xlsm"):
        return "xlsx"
    if lower.endswith(".csv") or lower.endswith(".txt") or lower.endswith(".tsv"):
        return "csv"
    raise ValueError("仅支持 CSV / Excel（.xlsx）文件")


def max_bytes_for_format(fmt: str) -> int:
    fmt = (fmt or "").lower()
    if fmt == "xlsx":
        return int(settings.FILE_IMPORT_XLSX_MAX_BYTES or 200 * 1024 * 1024)
    return int(settings.FILE_IMPORT_MAX_BYTES or 3 * 1024 * 1024 * 1024)


def _ns(workspace_id: int, file_id: str) -> str:
    return f"file-imports/{int(workspace_id)}/{file_id}"


def file_import_s3_uri(workspace_id: int, file_id: str, fmt: str) -> Optional[str]:
    """合并后数据文件的 s3:// URI（多副本中转）；无 S3 配置时为 None。"""
    if not file_import_shared_enabled():
        return None
    return build_shared_object_uri(_ns(workspace_id, file_id), f"data.{fmt}")


def file_import_storage_public(meta: Dict[str, Any]) -> Dict[str, Any]:
    """前端/API 可见的存储信息：内表装数为主，S3 仅中转提示。"""
    fmt = str(meta.get("format") or "csv")
    workspace_id = int(meta.get("workspace_id") or 0)
    file_id = str(meta.get("file_id") or "")
    uri = meta.get("s3_uri") or file_import_s3_uri(workspace_id, file_id, fmt)
    storage = meta.get("storage") or ("s3" if uri else "local")
    out: Dict[str, Any] = {
        "storage": storage,
        "s3_uri": uri,
        "load_mode": "internal_table",
        "storage_note": (
            "默认写入 Doris/MySQL 内表（Stream Load / 批量 INSERT）。"
            "对象存储副本仅用于多副本上传中转与排障，不建议当作长期查询外表。"
        ),
    }
    if uri and fmt == "csv":
        out["advanced_s3_tvf_hint"] = (
            f"-- 仅高级排障/二次探查；正式查询请用内表\n"
            f"-- Doris 需已配置 S3 凭证（endpoint/region/aksk 或 IRSA 等价能力）\n"
            f'SELECT * FROM S3(\n'
            f'  "uri" = "{uri}",\n'
            f'  "format" = "csv",\n'
            f'  "column_separator" = ",",\n'
            f'  "s3.endpoint" = "<your-endpoint>",\n'
            f'  "s3.region" = "<your-region>"\n'
            f") LIMIT 100;"
        )
    return out


def _folder(workspace_id: int, file_id: str) -> Path:
    return _root() / str(int(workspace_id)) / file_id


def _ck_cache_key(workspace_id: int, client_key: str) -> str:
    digest = hashlib.sha256(client_key.encode("utf-8")).hexdigest()[:40]
    return f"file_import:ck:{int(workspace_id)}:{digest}"


def _parts_cache_key(workspace_id: int, file_id: str) -> str:
    return f"file_import:parts:{int(workspace_id)}:{file_id}"


def _write_meta_local(folder: Path, meta: Dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _persist_meta(meta: Dict[str, Any]) -> None:
    workspace_id = int(meta["workspace_id"])
    file_id = str(meta["file_id"])
    folder = _folder(workspace_id, file_id)
    _write_meta_local(folder, meta)
    if file_import_shared_enabled():
        put_shared_object(
            _ns(workspace_id, file_id),
            "meta.json",
            json.dumps(meta, ensure_ascii=False).encode("utf-8"),
            "application/json",
        )
    _remember_client_key(meta)


def _remember_client_key(meta: Dict[str, Any]) -> None:
    client_key = (meta.get("client_key") or "").strip()
    if not client_key or meta.get("status") not in (None, "uploading"):
        return
    workspace_id = int(meta["workspace_id"])
    payload = {
        "file_id": meta["file_id"],
        "size_bytes": int(meta.get("size_bytes") or 0),
        "total_chunks": int(meta.get("total_chunks") or 0),
        "status": meta.get("status") or "uploading",
    }
    shared_state.cache_set(_ck_cache_key(workspace_id, client_key), payload, _SESSION_TTL_SEC)
    if file_import_shared_enabled():
        digest = hashlib.sha256(client_key.encode("utf-8")).hexdigest()[:40]
        try:
            put_shared_object(
                f"file-imports/_ck/{workspace_id}",
                f"{digest}.json",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json",
            )
        except Exception as ex:
            logger.debug("写入 file-import client_key 索引失败: %s", ex)


def _clear_client_key(workspace_id: int, client_key: Optional[str], file_id: Optional[str] = None) -> None:
    client_key = (client_key or "").strip()
    if not client_key:
        return
    # Redis：写入已结束状态，避免误续传
    shared_state.cache_set(
        _ck_cache_key(workspace_id, client_key),
        {"file_id": file_id, "status": "done"},
        3600,
    )
    if file_import_shared_enabled():
        digest = hashlib.sha256(client_key.encode("utf-8")).hexdigest()[:40]
        try:
            delete_shared_object(f"file-imports/_ck/{workspace_id}", f"{digest}.json")
        except Exception:
            pass


def _parts_redis_client():
    return shared_state.redis_client(required=False)


def _mark_part_received(workspace_id: int, file_id: str, idx: int) -> None:
    client = _parts_redis_client()
    if client is None:
        return
    rkey = shared_state.key("cache", _parts_cache_key(workspace_id, file_id))
    client.sadd(rkey, str(int(idx)))
    client.expire(rkey, _SESSION_TTL_SEC)


def _list_parts_redis(workspace_id: int, file_id: str, total: int) -> Optional[List[int]]:
    """Redis 集合存在时返回已收序号；key 不存在返回 None（需回退 S3/本地）。"""
    client = _parts_redis_client()
    if client is None:
        return None
    rkey = shared_state.key("cache", _parts_cache_key(workspace_id, file_id))
    if not client.exists(rkey):
        return None
    found: List[int] = []
    for item in client.smembers(rkey) or []:
        try:
            val = int(item.decode() if isinstance(item, (bytes, bytearray)) else item)
        except Exception:
            continue
        if 0 <= val < total:
            found.append(val)
    return sorted(found)


def _clear_parts_redis(workspace_id: int, file_id: str) -> None:
    client = _parts_redis_client()
    if client is None:
        return
    client.delete(shared_state.key("cache", _parts_cache_key(workspace_id, file_id)))


def save_upload(
    *,
    workspace_id: int,
    user_id: int,
    filename: str,
    content: bytes,
) -> Dict[str, Any]:
    """小文件便捷写入（测试用）；大文件请用 save_upload_stream。"""
    original = sanitize_filename(filename)
    fmt = detect_format(original)
    max_bytes = max_bytes_for_format(fmt)
    if len(content) > max_bytes:
        raise ValueError(f"文件超过上限 {max_bytes // (1024 * 1024)}MB（{fmt}）")
    if not content:
        raise ValueError("文件内容为空")

    file_id = uuid.uuid4().hex
    folder = _folder(workspace_id, file_id)
    folder.mkdir(parents=True, exist_ok=True)
    data_path = folder / f"data.{fmt}"
    data_path.write_bytes(content)
    meta = {
        "file_id": file_id,
        "workspace_id": int(workspace_id),
        "uploaded_by": int(user_id),
        "original_filename": original,
        "format": fmt,
        "size_bytes": len(content),
        "stored_path": str(data_path),
        "status": "ready",
        "storage": "s3" if file_import_shared_enabled() else "local",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if file_import_shared_enabled():
        put_shared_object(_ns(workspace_id, file_id), f"data.{fmt}", content, "application/octet-stream")
        meta["s3_uri"] = file_import_s3_uri(workspace_id, file_id, fmt)
    _persist_meta(meta)
    return meta


async def save_upload_stream(
    *,
    workspace_id: int,
    user_id: int,
    filename: str,
    chunks: AsyncIterator[bytes],
) -> Dict[str, Any]:
    """流式落盘，避免 2GB 文件整包进内存。"""
    original = sanitize_filename(filename)
    fmt = detect_format(original)
    max_bytes = max_bytes_for_format(fmt)

    file_id = uuid.uuid4().hex
    folder = _folder(workspace_id, file_id)
    folder.mkdir(parents=True, exist_ok=True)
    data_path = folder / f"data.{fmt}"
    size = 0
    try:
        with data_path.open("wb") as f:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(
                        f"文件超过上限 {max_bytes // (1024 * 1024)}MB"
                        + ("（Excel 大文件请先转为 CSV）" if fmt == "xlsx" else "（CSV 默认 ≤3GB）")
                    )
                f.write(chunk)
    except Exception:
        try:
            if data_path.is_file():
                data_path.unlink()
            folder.rmdir()
        except Exception:
            pass
        raise

    if size <= 0:
        try:
            data_path.unlink(missing_ok=True)
            folder.rmdir()
        except Exception:
            pass
        raise ValueError("文件内容为空")

    meta = {
        "file_id": file_id,
        "workspace_id": int(workspace_id),
        "uploaded_by": int(user_id),
        "original_filename": original,
        "format": fmt,
        "size_bytes": size,
        "stored_path": str(data_path),
        "status": "ready",
        "storage": "s3" if file_import_shared_enabled() else "local",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if file_import_shared_enabled():
        put_shared_object_file(
            _ns(workspace_id, file_id),
            f"data.{fmt}",
            str(data_path),
            "application/octet-stream",
        )
        meta["s3_uri"] = file_import_s3_uri(workspace_id, file_id, fmt)
    _persist_meta(meta)
    return meta


def load_meta(workspace_id: int, file_id: str) -> Dict[str, Any]:
    file_id = (file_id or "").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", file_id):
        raise ValueError("无效的 file_id")
    folder = _folder(workspace_id, file_id)
    meta_path = folder / "meta.json"
    meta: Optional[Dict[str, Any]] = None
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    elif file_import_shared_enabled():
        raw = get_shared_object(_ns(workspace_id, file_id), "meta.json")
        if raw:
            meta = json.loads(raw.decode("utf-8"))
            folder.mkdir(parents=True, exist_ok=True)
            meta_path.write_bytes(raw)
    if not meta:
        raise FileNotFoundError("上传文件不存在或已过期")
    if int(meta.get("workspace_id") or 0) != int(workspace_id):
        raise FileNotFoundError("上传文件不存在或不属于该工作空间")
    return meta


def resolve_data_path(meta: Dict[str, Any]) -> Path:
    workspace_id = int(meta.get("workspace_id") or 0)
    file_id = str(meta.get("file_id") or "")
    fmt = str(meta.get("format") or "csv")
    folder = _folder(workspace_id, file_id)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"data.{fmt}"

    p = Path(str(meta.get("stored_path") or ""))
    if p.is_file() and p.stat().st_size > 0:
        return p
    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    if meta.get("storage") == "s3" or file_import_shared_enabled():
        ok = download_shared_object_to_file(_ns(workspace_id, file_id), f"data.{fmt}", str(dest))
        if ok and dest.is_file() and dest.stat().st_size > 0:
            meta["stored_path"] = str(dest)
            return dest
    raise FileNotFoundError("上传文件数据已丢失")


def read_bytes(workspace_id: int, file_id: str) -> Tuple[Dict[str, Any], bytes]:
    """仅用于小文件测试；大文件请用 resolve_data_path 流式读。"""
    meta = load_meta(workspace_id, file_id)
    path = resolve_data_path(meta)
    return meta, path.read_bytes()


def init_chunked_upload(
    *,
    workspace_id: int,
    user_id: int,
    filename: str,
    size_bytes: int,
    total_chunks: int,
    client_key: Optional[str] = None,
    chunk_bytes: Optional[int] = None,
    force_new: bool = False,
) -> Dict[str, Any]:
    """
    初始化或复用分片会话。
    同一 client_key（前端指纹）若仍有 uploading 会话，则直接返回以便断点续传。
    force_new=True 时跳过续传（用于服务端会话已丢失）。
    """
    original = sanitize_filename(filename)
    fmt = detect_format(original)
    max_bytes = max_bytes_for_format(fmt)
    size_bytes = int(size_bytes or 0)
    total_chunks = int(total_chunks or 0)
    chunk_bytes = int(chunk_bytes or settings.FILE_IMPORT_CHUNK_BYTES or 8 * 1024 * 1024)
    client_key = (client_key or "").strip()[:200] or None
    if size_bytes <= 0:
        raise ValueError("文件大小无效")
    if size_bytes > max_bytes:
        raise ValueError(
            f"文件超过上限 {max_bytes // (1024 * 1024)}MB"
            + ("（Excel 大文件请先转为 CSV）" if fmt == "xlsx" else "（CSV 默认 ≤3GB）")
        )
    if total_chunks < 1 or total_chunks > 100_000:
        raise ValueError("分片数量无效")

    if client_key and not force_new:
        existing = _find_resumable_session(workspace_id, client_key, size_bytes, total_chunks)
        if existing:
            try:
                status = get_upload_status(workspace_id, existing["file_id"])
                return {
                    **status,
                    "resumed": True,
                    "chunk_bytes_hint": chunk_bytes,
                    "max_bytes": max_bytes,
                }
            except FileNotFoundError:
                _clear_client_key(workspace_id, client_key, existing.get("file_id"))

    # 单用户并发 uploading 上限
    max_conc = int(getattr(settings, "FILE_IMPORT_MAX_CONCURRENT_UPLOADS", 3) or 3)
    uploading_n = 0
    try:
        for d in _root().joinpath(str(int(workspace_id))).glob("*"):
            mp = d / "meta.json"
            if not mp.is_file():
                continue
            try:
                m = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                continue
            owner = m.get("user_id", m.get("uploaded_by"))
            if int(owner or 0) == int(user_id) and m.get("status") == "uploading":
                uploading_n += 1
    except Exception:
        pass
    if uploading_n >= max_conc:
        raise ValueError(f"并发上传过多（上限 {max_conc}），请先完成或取消进行中的上传")

    file_id = uuid.uuid4().hex
    folder = _folder(workspace_id, file_id)
    parts = folder / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    meta = {
        "file_id": file_id,
        "workspace_id": int(workspace_id),
        "uploaded_by": int(user_id),
        "original_filename": original,
        "format": fmt,
        "size_bytes": size_bytes,
        "total_chunks": total_chunks,
        "chunk_bytes": chunk_bytes,
        "client_key": client_key,
        "received_chunks": [],
        "status": "uploading",
        "storage": "s3" if file_import_shared_enabled() else "local",
        "stored_path": str(folder / f"data.{fmt}"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _persist_meta(meta)
    return {
        "file_id": file_id,
        "format": fmt,
        "size_bytes": size_bytes,
        "total_chunks": total_chunks,
        "received_chunks": [],
        "missing_chunks": list(range(total_chunks)),
        "received": 0,
        "percent": 0,
        "status": "uploading",
        "resumed": False,
        "chunk_bytes_hint": chunk_bytes,
        "max_bytes": max_bytes,
        "client_key": client_key,
        "storage": meta["storage"],
    }


def _reconcile_received_local(folder: Path, meta: Dict[str, Any]) -> List[int]:
    parts = folder / "parts"
    total = int(meta.get("total_chunks") or 0)
    found: set[int] = set()
    if parts.is_dir():
        for p in parts.glob("*.part"):
            try:
                idx = int(p.stem)
            except ValueError:
                continue
            if 0 <= idx < total and p.stat().st_size > 0:
                found.add(idx)
    meta_received = set(int(x) for x in (meta.get("received_chunks") or []))
    merged = sorted(
        found
        | {
            i
            for i in meta_received
            if 0 <= i < total and (parts / f"{i:06d}.part").is_file()
        }
    )
    return merged


def _reconcile_received_s3(workspace_id: int, file_id: str, total: int) -> List[int]:
    found: set[int] = set()
    for name in list_shared_object_names(_ns(workspace_id, file_id), "parts"):
        # parts/000012.part
        base = Path(name).name
        if not base.endswith(".part"):
            continue
        try:
            idx = int(base[: -len(".part")])
        except ValueError:
            continue
        if 0 <= idx < total:
            found.add(idx)
    return sorted(found)


def _reconcile_received(workspace_id: int, file_id: str, meta: Dict[str, Any]) -> List[int]:
    """合并 Redis / S3 / 本地已收分片。禁止只信 Redis（易漏片导致 complete 误报缺失）。"""
    total = int(meta.get("total_chunks") or 0)
    found: set[int] = set()

    redis_parts = _list_parts_redis(workspace_id, file_id, total)
    if redis_parts is not None:
        found.update(redis_parts)

    folder = _folder(workspace_id, file_id)
    found.update(_reconcile_received_local(folder, meta))

    if meta.get("storage") == "s3" or file_import_shared_enabled():
        try:
            found.update(_reconcile_received_s3(workspace_id, file_id, total))
        except Exception as ex:
            logger.warning(
                "列举 S3 file-import 分片失败 ws=%s file_id=%s: %s",
                workspace_id,
                file_id,
                ex,
            )

    return sorted(i for i in found if 0 <= i < total)


def _find_resumable_session(
    workspace_id: int,
    client_key: str,
    size_bytes: int,
    total_chunks: int,
) -> Optional[Dict[str, Any]]:
    cached = shared_state.cache_get(_ck_cache_key(workspace_id, client_key))
    if isinstance(cached, dict) and cached.get("status") == "uploading":
        if (
            int(cached.get("size_bytes") or 0) == int(size_bytes)
            and int(cached.get("total_chunks") or 0) == int(total_chunks)
            and cached.get("file_id")
        ):
            try:
                return load_meta(workspace_id, str(cached["file_id"]))
            except FileNotFoundError:
                pass

    if file_import_shared_enabled():
        digest = hashlib.sha256(client_key.encode("utf-8")).hexdigest()[:40]
        raw = get_shared_object(f"file-imports/_ck/{workspace_id}", f"{digest}.json")
        if raw:
            try:
                idx = json.loads(raw.decode("utf-8"))
            except Exception:
                idx = None
            if (
                isinstance(idx, dict)
                and idx.get("status") == "uploading"
                and int(idx.get("size_bytes") or 0) == int(size_bytes)
                and int(idx.get("total_chunks") or 0) == int(total_chunks)
                and idx.get("file_id")
            ):
                try:
                    return load_meta(workspace_id, str(idx["file_id"]))
                except FileNotFoundError:
                    pass

    # 本地扫描（单副本 / 开发）
    base = _root() / str(int(workspace_id))
    if not base.is_dir():
        return None
    candidates: List[tuple[float, Dict[str, Any]]] = []
    for child in base.iterdir():
        meta_path = child / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("client_key") != client_key:
            continue
        if meta.get("status") != "uploading":
            continue
        if int(meta.get("size_bytes") or 0) != int(size_bytes):
            continue
        if int(meta.get("total_chunks") or 0) != int(total_chunks):
            continue
        try:
            mtime = meta_path.stat().st_mtime
        except Exception:
            mtime = 0.0
        candidates.append((mtime, meta))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def get_upload_status(workspace_id: int, file_id: str) -> Dict[str, Any]:
    meta = load_meta(workspace_id, file_id)
    total = int(meta.get("total_chunks") or 0)
    received = _reconcile_received(workspace_id, file_id, meta)
    # 多副本下勿频繁回写 meta（并发丢更新）；仅本地模式校准
    if not file_import_shared_enabled() and received != list(meta.get("received_chunks") or []):
        folder = _folder(workspace_id, file_id)
        meta["received_chunks"] = received
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_meta_local(folder, meta)
    missing = [i for i in range(total) if i not in set(received)]
    percent = int(round(100.0 * len(received) / total)) if total else 0
    return {
        "file_id": meta.get("file_id"),
        "status": meta.get("status"),
        "original_filename": meta.get("original_filename"),
        "format": meta.get("format"),
        "size_bytes": meta.get("size_bytes"),
        "total_chunks": total,
        "chunk_bytes": meta.get("chunk_bytes"),
        "client_key": meta.get("client_key"),
        "received_chunks": received,
        "missing_chunks": missing,
        "received": len(received),
        "percent": min(99, percent) if meta.get("status") == "uploading" else percent,
        "storage": meta.get("storage") or ("s3" if file_import_shared_enabled() else "local"),
    }


def _parts_redis_count(workspace_id: int, file_id: str) -> Optional[int]:
    client = _parts_redis_client()
    if client is None:
        return None
    rkey = shared_state.key("cache", _parts_cache_key(workspace_id, file_id))
    if not client.exists(rkey):
        return None
    return int(client.scard(rkey) or 0)


def save_upload_chunk(
    *,
    workspace_id: int,
    file_id: str,
    chunk_index: int,
    content: bytes,
    expected_sha256: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    meta = load_meta(workspace_id, file_id)
    assert_upload_owner(meta, user_id)
    if meta.get("status") not in (None, "uploading"):
        raise ValueError("该上传会话已结束，请重新选择文件")
    max_chunk = int(settings.FILE_IMPORT_CHUNK_BYTES or 16 * 1024 * 1024) * 2
    if len(content) > max_chunk:
        raise ValueError(f"单片过大: {len(content)} > {max_chunk}")
    total = int(meta.get("total_chunks") or 0)
    idx = int(chunk_index)
    if idx < 0 or (total and idx >= total):
        raise ValueError(f"分片序号无效: {idx}")
    if not content:
        raise ValueError("空分片")
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 and expected_sha256.lower() != digest:
        raise ValueError(f"分片 checksum 不匹配 index={idx}")

    folder = _folder(workspace_id, file_id)
    parts = folder / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    part_name = f"{idx:06d}.part"
    part_path = parts / part_name
    skipped = False
    use_s3 = file_import_shared_enabled() or meta.get("storage") == "s3"

    if use_s3:
        s3_name = f"parts/{part_name}"
        put_shared_object(
            _ns(workspace_id, file_id),
            s3_name,
            content,
            "application/octet-stream",
        )
        try:
            tmp_path = parts / f"{part_name}.tmp"
            tmp_path.write_bytes(content)
            tmp_path.replace(part_path)
        except Exception:
            logger.debug("file-import 本地分片缓存失败 idx=%s", idx, exc_info=True)
    else:
        if part_path.is_file() and part_path.stat().st_size == len(content):
            skipped = True
        else:
            tmp_path = parts / f"{part_name}.tmp"
            tmp_path.write_bytes(content)
            tmp_path.replace(part_path)

    _mark_part_received(workspace_id, file_id, idx)
    # 记录分片 digest（便于排障；不强制全量保存）
    digests = dict(meta.get("chunk_sha256") or {})
    digests[str(idx)] = digest
    meta["chunk_sha256"] = digests
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _persist_meta(meta)
    except Exception:
        pass

    if use_s3:
        redis_n = _parts_redis_count(workspace_id, file_id)
        received_n = int(redis_n) if redis_n is not None else max(1, idx + 1)
    else:
        received = set(_reconcile_received_local(folder, meta))
        received.add(idx)
        meta["received_chunks"] = sorted(received)
        meta["status"] = "uploading"
        _write_meta_local(folder, meta)
        received_n = len(received)

    return {
        "file_id": file_id,
        "chunk_index": idx,
        "skipped": skipped,
        "sha256": digest,
        "received": received_n,
        "total_chunks": total,
        "percent": int(round(100.0 * received_n / total)) if total else 0,
    }


def abort_chunked_upload(*, workspace_id: int, file_id: str) -> Dict[str, Any]:
    meta = load_meta(workspace_id, file_id)
    folder = _folder(workspace_id, file_id)
    meta["status"] = "aborted"
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _persist_meta(meta)
    _clear_client_key(workspace_id, meta.get("client_key"), file_id)
    _clear_parts_redis(workspace_id, file_id)

    parts = folder / "parts"
    if parts.is_dir():
        for p in parts.glob("*"):
            try:
                p.unlink()
            except Exception:
                pass
        try:
            parts.rmdir()
        except Exception:
            pass
    data = folder / f"data.{meta.get('format') or 'csv'}"
    try:
        data.unlink(missing_ok=True)
    except Exception:
        pass

    if file_import_shared_enabled() or meta.get("storage") == "s3":
        try:
            delete_shared_objects_with_prefix(_ns(workspace_id, file_id), "parts")
            fmt = str(meta.get("format") or "csv")
            delete_shared_object(_ns(workspace_id, file_id), f"data.{fmt}")
        except Exception as ex:
            logger.warning("清理 S3 file-import 分片失败 file_id=%s: %s", file_id, ex)

    return {"file_id": file_id, "status": "aborted"}


def assert_upload_owner(meta: Dict[str, Any], user_id: Optional[int]) -> None:
    if user_id is None:
        return
    owner = meta.get("user_id", meta.get("uploaded_by"))
    if owner is not None and int(owner) != int(user_id):
        raise ValueError("无权访问该上传会话")


def _finalize_lock_key(workspace_id: int, file_id: str) -> str:
    return f"file-import-finalize:{int(workspace_id)}:{file_id}"


def finalize_chunked_upload(*, workspace_id: int, file_id: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    meta = load_meta(workspace_id, file_id)
    assert_upload_owner(meta, user_id)
    fmt = str(meta.get("format") or "csv")
    folder = _folder(workspace_id, file_id)
    data_path = folder / f"data.{fmt}"

    if meta.get("status") == "ready":
        try:
            resolve_data_path(meta)
            return meta
        except FileNotFoundError:
            pass
    if meta.get("status") == "aborted":
        raise ValueError("上传会话已取消")
    if meta.get("status") == "finalizing":
        raise ValueError("正在合并分片，请稍后重试")

    from app.services.distributed_lock import acquire_distributed_lock

    lock = acquire_distributed_lock(_finalize_lock_key(workspace_id, file_id))
    if lock is None:
        raise ValueError("其他节点正在合并该上传，请稍后重试")
    try:
        # 重新加载，避免双 complete
        meta = load_meta(workspace_id, file_id)
        if meta.get("status") == "ready":
            resolve_data_path(meta)
            return meta
        meta["status"] = "finalizing"
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        _persist_meta(meta)

        total = int(meta.get("total_chunks") or 0)
        received = _reconcile_received(workspace_id, file_id, meta)
        if total <= 0 or len(received) != total or set(received) != set(range(total)):
            missing = [i for i in range(total) if i not in set(received)]
            raise ValueError(f"分片不完整，缺失 {len(missing)} 片（如 {missing[:8]}）")

        expected = int(meta.get("size_bytes") or 0)
        written = 0
        hasher = hashlib.sha256()
        folder.mkdir(parents=True, exist_ok=True)
        parts = folder / "parts"
        parts.mkdir(parents=True, exist_ok=True)

        with data_path.open("wb") as out:
            for i in range(total):
                part_name = f"{i:06d}.part"
                part = parts / part_name
                if not part.is_file() or part.stat().st_size <= 0:
                    if file_import_shared_enabled() or meta.get("storage") == "s3":
                        ok = download_shared_object_to_file(
                            _ns(workspace_id, file_id),
                            f"parts/{part_name}",
                            str(part),
                        )
                        if not ok or not part.is_file():
                            raise ValueError(f"缺失分片文件: {i}")
                    else:
                        raise ValueError(f"缺失分片文件: {i}")
                raw = part.read_bytes()
                out.write(raw)
                hasher.update(raw)
                written += len(raw)
                part.unlink(missing_ok=True)
        try:
            parts.rmdir()
        except Exception:
            pass

        if expected and written != expected:
            if abs(written - expected) > max(1024, expected // 1000):
                raise ValueError(f"合并后大小不符: expect={expected}, got={written}")

        content_sha = hasher.hexdigest()
        if file_import_shared_enabled() or meta.get("storage") == "s3":
            put_shared_object_file(
                _ns(workspace_id, file_id),
                f"data.{fmt}",
                str(data_path),
                "application/octet-stream",
            )
            try:
                delete_shared_objects_with_prefix(_ns(workspace_id, file_id), "parts")
            except Exception as ex:
                logger.warning("合并后清理 S3 parts 失败 file_id=%s: %s", file_id, ex)
            meta["storage"] = "s3"
            meta["s3_uri"] = file_import_s3_uri(workspace_id, file_id, fmt)

        meta["size_bytes"] = written
        meta["content_sha256"] = content_sha
        meta["stored_path"] = str(data_path)
        meta["status"] = "ready"
        meta["received_chunks"] = list(range(total))
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        _persist_meta(meta)
        _clear_client_key(workspace_id, meta.get("client_key"), file_id)
        _clear_parts_redis(workspace_id, file_id)
        return meta
    except Exception:
        try:
            meta["status"] = "uploading"
            _persist_meta(meta)
        except Exception:
            pass
        raise
    finally:
        lock.release()


def cleanup_orphan_uploads(*, older_than_hours: Optional[int] = None) -> Dict[str, Any]:
    """回收未引用的 ready/aborted 上传（引用感知：检查 FileImportVersion.file_id）。"""
    from app.core.database import SessionLocal
    from app.models.workspace import FileImportVersion

    hours = int(older_than_hours or getattr(settings, "FILE_IMPORT_ORPHAN_TTL_HOURS", 72) or 72)
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    removed = 0
    scanned = 0
    root = _root()
    db = SessionLocal()
    try:
        referenced = {
            str(r[0])
            for r in db.query(FileImportVersion.file_id).distinct().all()
            if r and r[0]
        }
        for ws_dir in root.glob("*"):
            if not ws_dir.is_dir() or not ws_dir.name.isdigit():
                continue
            for file_dir in ws_dir.iterdir():
                if not file_dir.is_dir():
                    continue
                scanned += 1
                file_id = file_dir.name
                if file_id in referenced:
                    continue
                meta_path = file_dir / "meta.json"
                try:
                    mtime = meta_path.stat().st_mtime if meta_path.is_file() else file_dir.stat().st_mtime
                except Exception:
                    continue
                if mtime > cutoff:
                    continue
                try:
                    import shutil

                    shutil.rmtree(file_dir, ignore_errors=True)
                    if file_import_shared_enabled():
                        delete_shared_objects_with_prefix(_ns(int(ws_dir.name), file_id), "")
                    removed += 1
                except Exception as ex:
                    logger.warning("cleanup orphan failed %s: %s", file_id, ex)
    finally:
        db.close()
    return {"scanned": scanned, "removed": removed, "ttl_hours": hours}
