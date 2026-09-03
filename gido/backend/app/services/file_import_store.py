# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""本地文件导入：上传文件流式落盘与元数据。"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

from app.core.config import settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\u4e00-\u9fff-]+")


def _root() -> Path:
    d = Path(settings.FILE_IMPORT_UPLOAD_DIR).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


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


def _write_meta(folder: Path, meta: Dict[str, Any]) -> None:
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


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
    folder = _root() / str(int(workspace_id)) / file_id
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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_meta(folder, meta)
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
    folder = _root() / str(int(workspace_id)) / file_id
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
        # 清理半成品
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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_meta(folder, meta)
    return meta


def load_meta(workspace_id: int, file_id: str) -> Dict[str, Any]:
    file_id = (file_id or "").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", file_id):
        raise ValueError("无效的 file_id")
    meta_path = _root() / str(int(workspace_id)) / file_id / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError("上传文件不存在或已过期")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if int(meta.get("workspace_id") or 0) != int(workspace_id):
        raise FileNotFoundError("上传文件不存在或不属于该工作空间")
    return meta


def resolve_data_path(meta: Dict[str, Any]) -> Path:
    p = Path(str(meta.get("stored_path") or ""))
    if not p.is_file():
        raise FileNotFoundError("上传文件数据已丢失")
    return p


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
) -> Dict[str, Any]:
    """
    初始化或复用分片会话。
    同一 client_key（前端指纹）若仍有 uploading 会话，则直接返回以便断点续传。
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

    # 断点续传：复用同指纹未完成会话
    if client_key:
        existing = _find_resumable_session(workspace_id, client_key, size_bytes, total_chunks)
        if existing:
            status = get_upload_status(workspace_id, existing["file_id"])
            return {
                **status,
                "resumed": True,
                "chunk_bytes_hint": chunk_bytes,
                "max_bytes": max_bytes,
            }

    file_id = uuid.uuid4().hex
    folder = _root() / str(int(workspace_id)) / file_id
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
        "stored_path": str(folder / f"data.{fmt}"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_meta(folder, meta)
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
    }


def _reconcile_received(folder: Path, meta: Dict[str, Any]) -> List[int]:
    """以磁盘 parts 为准校准已收分片，避免进程崩溃后 meta 落后。"""
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
    merged = sorted(found | {i for i in meta_received if 0 <= i < total and (parts / f"{i:06d}.part").is_file()})
    return merged


def _find_resumable_session(
    workspace_id: int,
    client_key: str,
    size_bytes: int,
    total_chunks: int,
) -> Optional[Dict[str, Any]]:
    base = _root() / str(int(workspace_id))
    if not base.is_dir():
        return None
    # 最近修改的优先
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
    folder = _root() / str(int(workspace_id)) / file_id
    total = int(meta.get("total_chunks") or 0)
    received = _reconcile_received(folder, meta)
    if received != list(meta.get("received_chunks") or []):
        meta["received_chunks"] = received
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_meta(folder, meta)
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
    }


def save_upload_chunk(
    *,
    workspace_id: int,
    file_id: str,
    chunk_index: int,
    content: bytes,
) -> Dict[str, Any]:
    meta = load_meta(workspace_id, file_id)
    if meta.get("status") not in (None, "uploading"):
        raise ValueError("该上传会话已结束，请重新选择文件")
    total = int(meta.get("total_chunks") or 0)
    idx = int(chunk_index)
    if idx < 0 or (total and idx >= total):
        raise ValueError(f"分片序号无效: {idx}")
    if not content:
        raise ValueError("空分片")

    folder = _root() / str(int(workspace_id)) / file_id
    parts = folder / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    part_path = parts / f"{idx:06d}.part"

    # 幂等：已有同大小分片则跳过写入
    skipped = False
    if part_path.is_file() and part_path.stat().st_size == len(content):
        skipped = True
    else:
        # 先写临时文件再 rename，避免半片落盘
        tmp_path = parts / f"{idx:06d}.part.tmp"
        tmp_path.write_bytes(content)
        tmp_path.replace(part_path)

    received = set(_reconcile_received(folder, meta))
    received.add(idx)
    meta["received_chunks"] = sorted(received)
    meta["status"] = "uploading"
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_meta(folder, meta)
    return {
        "file_id": file_id,
        "chunk_index": idx,
        "skipped": skipped,
        "received": len(received),
        "total_chunks": total,
        "percent": int(round(100.0 * len(received) / total)) if total else 0,
    }


def abort_chunked_upload(*, workspace_id: int, file_id: str) -> Dict[str, Any]:
    meta = load_meta(workspace_id, file_id)
    folder = _root() / str(int(workspace_id)) / file_id
    meta["status"] = "aborted"
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_meta(folder, meta)
    # 清理 parts，保留 meta 便于审计
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
    return {"file_id": file_id, "status": "aborted"}


def finalize_chunked_upload(*, workspace_id: int, file_id: str) -> Dict[str, Any]:
    meta = load_meta(workspace_id, file_id)
    if meta.get("status") == "ready" and Path(str(meta.get("stored_path") or "")).is_file():
        return meta
    if meta.get("status") == "aborted":
        raise ValueError("上传会话已取消")

    folder = _root() / str(int(workspace_id)) / file_id
    total = int(meta.get("total_chunks") or 0)
    received = _reconcile_received(folder, meta)
    meta["received_chunks"] = received
    if total <= 0 or len(received) != total or set(received) != set(range(total)):
        missing = [i for i in range(total) if i not in set(received)]
        raise ValueError(f"分片不完整，缺失 {len(missing)} 片（如 {missing[:8]}）")

    parts = folder / "parts"
    fmt = str(meta.get("format") or "csv")
    data_path = folder / f"data.{fmt}"
    expected = int(meta.get("size_bytes") or 0)
    written = 0
    with data_path.open("wb") as out:
        for i in range(total):
            part = parts / f"{i:06d}.part"
            if not part.is_file():
                raise ValueError(f"缺失分片文件: {i}")
            raw = part.read_bytes()
            out.write(raw)
            written += len(raw)
            part.unlink(missing_ok=True)
    try:
        parts.rmdir()
    except Exception:
        pass

    if expected and written != expected:
        if abs(written - expected) > max(1024, expected // 1000):
            raise ValueError(f"合并后大小不符: expect={expected}, got={written}")

    meta["size_bytes"] = written
    meta["stored_path"] = str(data_path)
    meta["status"] = "ready"
    meta["received_chunks"] = list(range(total))
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_meta(folder, meta)
    return meta
