# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""本地文件导入：上传文件流式落盘与元数据。"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Tuple, Union

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
) -> Dict[str, Any]:
    original = sanitize_filename(filename)
    fmt = detect_format(original)
    max_bytes = max_bytes_for_format(fmt)
    size_bytes = int(size_bytes or 0)
    total_chunks = int(total_chunks or 0)
    if size_bytes <= 0:
        raise ValueError("文件大小无效")
    if size_bytes > max_bytes:
        raise ValueError(
            f"文件超过上限 {max_bytes // (1024 * 1024)}MB"
            + ("（Excel 大文件请先转为 CSV）" if fmt == "xlsx" else "（CSV 默认 ≤3GB）")
        )
    if total_chunks < 1 or total_chunks > 100_000:
        raise ValueError("分片数量无效")

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
        "received_chunks": [],
        "status": "uploading",
        "stored_path": str(folder / f"data.{fmt}"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_meta(folder, meta)
    return {
        "file_id": file_id,
        "format": fmt,
        "size_bytes": size_bytes,
        "total_chunks": total_chunks,
        "chunk_bytes_hint": int(settings.FILE_IMPORT_CHUNK_BYTES or 8 * 1024 * 1024),
        "max_bytes": max_bytes,
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
    part_path.write_bytes(content)

    received = set(int(x) for x in (meta.get("received_chunks") or []))
    received.add(idx)
    meta["received_chunks"] = sorted(received)
    meta["status"] = "uploading"
    _write_meta(folder, meta)
    return {
        "file_id": file_id,
        "chunk_index": idx,
        "received": len(received),
        "total_chunks": total,
    }


def finalize_chunked_upload(*, workspace_id: int, file_id: str) -> Dict[str, Any]:
    meta = load_meta(workspace_id, file_id)
    total = int(meta.get("total_chunks") or 0)
    received = sorted(int(x) for x in (meta.get("received_chunks") or []))
    if total <= 0 or len(received) != total or received != list(range(total)):
        missing = [i for i in range(total) if i not in set(received)]
        raise ValueError(f"分片不完整，缺失 {len(missing)} 片（如 {missing[:8]}）")

    folder = _root() / str(int(workspace_id)) / file_id
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
        # 允许前端 size 与实际略有差异时仍接受，但偏差过大则失败
        if abs(written - expected) > max(1024, expected // 1000):
            raise ValueError(f"合并后大小不符: expect={expected}, got={written}")

    meta["size_bytes"] = written
    meta["stored_path"] = str(data_path)
    meta["status"] = "ready"
    meta["received_chunks"] = list(range(total))
    _write_meta(folder, meta)
    return meta
