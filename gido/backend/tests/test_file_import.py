# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""本地文件导入单元测试：解析 / DDL / 流式落盘 / Stream Load 打桩。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.file_import_parse import (
    coerce_cell,
    infer_columns,
    parse_csv_bytes,
    sanitize_column_name,
)
from app.services.file_import_exec import (
    build_create_table_ddl,
    doris_http_port,
    normalize_columns,
    validate_table_name,
)
from app.services.file_import_store import max_bytes_for_format, save_upload_stream


class _Ds:
    def __init__(self, ds_type: str, database: str = "test", port: int = 9030, extra=None):
        self.ds_type = ds_type
        self.database = database
        self.host = "127.0.0.1"
        self.port = port
        self.username = "u"
        self.password = "p"
        self.extra_config = extra or {}


def test_sanitize_column_name_unique():
    used: set[str] = set()
    assert sanitize_column_name("订单 ID", used, 0) == "订单_ID"
    assert sanitize_column_name("订单 ID", used, 1).startswith("订单_ID")


def test_parse_csv_infer_types():
    raw = b"id,name,amount,paid_at\n1,alice,12.5,2024-01-02 03:04:05\n2,bob,3,2024-01-03\n"
    parsed = parse_csv_bytes(raw, has_header=True, preview_rows=10)
    assert parsed["row_count"] == 2
    types = {c["name"]: c["type"] for c in parsed["columns"]}
    assert types["id"] == "bigint"
    assert types["name"] == "string"
    assert types["amount"] == "double"
    assert types["paid_at"] == "datetime"


def test_coerce_cell():
    assert coerce_cell("42", "bigint") == 42
    assert coerce_cell("true", "boolean") == 1
    assert coerce_cell("2024-01-02", "datetime") == "2024-01-02 00:00:00"
    assert coerce_cell("", "string") is None


def test_mysql_ddl():
    cols = normalize_columns(
        [
            {"name": "id", "type": "bigint", "is_primary_key": True, "nullable": False},
            {"name": "name", "type": "string", "nullable": True},
        ]
    )
    ddl = build_create_table_ddl(_Ds("mysql", port=3306), "import_demo", cols)
    assert "CREATE TABLE IF NOT EXISTS `import_demo`" in ddl
    assert "PRIMARY KEY (`id`)" in ddl


def test_doris_ddl():
    cols = normalize_columns(
        [
            {"name": "id", "type": "bigint", "is_primary_key": True},
            {"name": "name", "type": "string"},
        ]
    )
    ddl = build_create_table_ddl(_Ds("doris"), "import_demo", cols)
    assert "DUPLICATE KEY(`id`)" in ddl
    assert "DISTRIBUTED BY HASH(`id`)" in ddl


def test_validate_table_name():
    assert validate_table_name("orders_1") == "orders_1"
    try:
        validate_table_name("1bad")
        assert False
    except ValueError:
        pass


def test_infer_columns_merge():
    cols = infer_columns(["a"], [["1"], ["1.5"]])
    assert cols[0]["type"] == "double"


def test_parse_csv_path_streaming(tmp_path):
    from app.services.file_import_parse import parse_csv_path, iter_csv_rows_from_path

    p = tmp_path / "big.csv"
    lines = ["id,name"] + [f"{i},n{i}" for i in range(1500)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    parsed = parse_csv_path(p, has_header=True, preview_rows=10, infer_rows=100)
    assert parsed["row_count"] == 1500
    assert len(parsed["preview_rows"]) == 10
    assert "all_rows" not in parsed
    n = sum(1 for _ in iter_csv_rows_from_path(p, has_header=True))
    assert n == 1500


def test_max_bytes_for_format_regression(monkeypatch):
    """回归：大文件能力不得回退到 50MB。"""
    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_MAX_BYTES", 3 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_XLSX_MAX_BYTES", 200 * 1024 * 1024)
    assert max_bytes_for_format("csv") >= 2 * 1024 * 1024 * 1024
    assert max_bytes_for_format("xlsx") == 200 * 1024 * 1024


def test_save_upload_stream(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_UPLOAD_DIR", str(tmp_path))

    async def _chunks():
        yield b"id,name\n"
        yield b"1,a\n"
        yield b"2,b\n"

    meta = asyncio.run(
        save_upload_stream(workspace_id=1, user_id=1, filename="demo.csv", chunks=_chunks())
    )
    assert meta["size_bytes"] == 16
    assert Path(meta["stored_path"]).is_file()
    assert Path(meta["stored_path"]).read_bytes().startswith(b"id,name")


def test_doris_http_port_extra_and_default():
    assert doris_http_port(_Ds("doris", port=9030)) == 8030
    assert doris_http_port(_Ds("doris", port=9030, extra={"http_port": 8040})) == 8040


def test_doris_stream_load_mocked(tmp_path):
    from app.services.file_import_exec import _doris_stream_load

    p = tmp_path / "data.csv"
    p.write_text("1,a\n2,b\n", encoding="utf-8")
    cols = [{"name": "id", "type": "bigint"}, {"name": "name", "type": "string"}]
    ds = _Ds("doris", database="demo", port=9030)

    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "Status": "Success",
        "NumberLoadedRows": 2,
        "NumberTotalRows": 2,
        "NumberFilteredRows": 0,
    }
    with patch("app.services.file_import_exec.requests.put", return_value=fake) as put:
        read, written = _doris_stream_load(
            ds, "t1", cols, p, column_separator=",", skip_header=False
        )
    assert read == 2 and written == 2
    assert put.call_args.kwargs["timeout"] == 3600
    assert "/api/demo/t1/_stream_load" in put.call_args.args[0]


def test_chunked_upload_assemble(tmp_path, monkeypatch):
    from app.services.file_import_store import (
        init_chunked_upload,
        save_upload_chunk,
        finalize_chunked_upload,
        resolve_data_path,
    )

    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_UPLOAD_DIR", str(tmp_path))
    raw = b"id,name\n1,a\n2,b\n3,c\n"
    parts = [raw[:8], raw[8:16], raw[16:]]
    init = init_chunked_upload(
        workspace_id=9,
        user_id=1,
        filename="demo.csv",
        size_bytes=len(raw),
        total_chunks=len(parts),
    )
    fid = init["file_id"]
    for i, p in enumerate(parts):
        save_upload_chunk(workspace_id=9, file_id=fid, chunk_index=i, content=p)
    meta = finalize_chunked_upload(workspace_id=9, file_id=fid)
    assert meta["status"] == "ready"
    assert resolve_data_path(meta).read_bytes() == raw
