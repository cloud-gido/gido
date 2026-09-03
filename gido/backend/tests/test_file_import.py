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
    def __init__(
        self,
        ds_type: str,
        database: str = "test",
        port: int = 9030,
        extra=None,
        username: str = "u",
        password: str = "p",
    ):
        self.ds_type = ds_type
        self.database = database
        self.host = "127.0.0.1"
        self.port = port
        self.username = username
        self.password = password
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
    assert put.call_args.kwargs["allow_redirects"] is False
    assert put.call_args.kwargs["auth"] == ("u", "p")
    assert "/api/demo/t1/_stream_load" in put.call_args.args[0]
    hdrs = put.call_args.kwargs["headers"]
    assert "skip_header" not in hdrs
    assert hdrs["enclose"] == '"'
    assert hdrs["escape"] == '"'


def test_doris_stream_load_skip_header_is_integer(tmp_path):
    """Doris skip_header 必须是跳过行数，不能传 true。"""
    from app.services.file_import_exec import _doris_stream_load

    p = tmp_path / "data.csv"
    p.write_text("id,name\n1,a\n", encoding="utf-8")
    cols = [{"name": "id", "type": "bigint"}, {"name": "name", "type": "string"}]
    ds = _Ds("doris", database="demo", port=9030)
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "Status": "Success",
        "NumberLoadedRows": 1,
        "NumberTotalRows": 1,
        "NumberFilteredRows": 0,
    }
    with patch("app.services.file_import_exec.requests.put", return_value=fake) as put:
        _doris_stream_load(ds, "t1", cols, p, skip_header=True)
    assert put.call_args.kwargs["headers"]["skip_header"] == "1"


def test_doris_stream_load_control_separator_uses_hex_header(tmp_path):
    """控制字符分隔符应以 \\xHH 形式放到 HTTP 头，避免 FE/网关 400。"""
    from app.services.file_import_exec import _doris_stream_load

    p = tmp_path / "data.csv"
    p.write_text("1,a\n", encoding="utf-8")
    cols = [{"name": "id", "type": "bigint"}, {"name": "name", "type": "string"}]
    ds = _Ds("doris", database="demo", port=9030)
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "Status": "Success",
        "NumberLoadedRows": 1,
        "NumberTotalRows": 1,
        "NumberFilteredRows": 0,
    }
    with patch("app.services.file_import_exec.requests.put", return_value=fake) as put:
        _doris_stream_load(ds, "t1", cols, p, column_separator="\x01")
    assert put.call_args.kwargs["headers"]["column_separator"] == "\\x01"


def test_doris_stream_load_retries_on_http_400_empty_message(tmp_path):
    """HTTP 400 且 Message 为空时，应按备选 header 再请求一次。"""
    from app.services.file_import_exec import _doris_stream_load

    p = tmp_path / "data.csv"
    p.write_text("1,a\n", encoding="utf-8")
    cols = [{"name": "id", "type": "bigint"}, {"name": "name", "type": "string"}]
    ds = _Ds("doris", database="demo", port=9030)

    bad = MagicMock()
    bad.status_code = 400
    bad.json.return_value = {"Status": "Fail", "Message": ""}

    good = MagicMock()
    good.status_code = 200
    good.json.return_value = {
        "Status": "Success",
        "NumberLoadedRows": 1,
        "NumberTotalRows": 1,
        "NumberFilteredRows": 0,
    }

    with patch(
        "app.services.file_import_exec.requests.put", side_effect=[bad, good]
    ) as put:
        read, written = _doris_stream_load(ds, "t1", cols, p, column_separator="\x01")

    assert read == 1 and written == 1
    # 第二次请求应使用不同的 column_separator header 表达
    assert put.call_count == 2
    assert (
        put.call_args_list[0].kwargs["headers"]["column_separator"]
        != put.call_args_list[1].kwargs["headers"]["column_separator"]
    )


def test_doris_stream_load_ads_database_defaults_to_ods(tmp_path):
    """ds.database=*_ads 时：默认 stream load URL 应落到 *_ods。"""
    from app.services.file_import_exec import _doris_stream_load

    p = tmp_path / "data.csv"
    p.write_text("1,a\n", encoding="utf-8")
    cols = [{"name": "id", "type": "bigint"}, {"name": "name", "type": "string"}]
    ds = _Ds("doris", database="bigdata_ads", port=9030)

    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "Status": "Success",
        "NumberLoadedRows": 1,
        "NumberTotalRows": 1,
        "NumberFilteredRows": 0,
    }

    with patch("app.services.file_import_exec.requests.put", return_value=fake) as put:
        _doris_stream_load(ds, "t1", cols, p)

    assert "/api/bigdata_ods/t1/_stream_load" in put.call_args.args[0]


def test_csv_to_stream_load_temp_strips_header(tmp_path):
    from app.services.file_import_exec import _DORIS_CSV_SEPARATOR, _csv_to_stream_load_temp

    p = tmp_path / "auth.csv"
    p.write_text("id,name,created_at\n1,alice,2024-01-01 00:00:00\n2,bob,2024-01-02 00:00:00\n", encoding="utf-8")
    cols = [
        {"name": "id", "type": "bigint"},
        {"name": "name", "type": "string"},
        {"name": "created_at", "type": "datetime"},
    ]
    out, n = _csv_to_stream_load_temp(
        p, encoding="utf-8", delimiter=",", has_header=True, cols=cols, max_rows=1000
    )
    try:
        text = out.read_text(encoding="utf-8")
        assert n == 2
        assert "id,name" not in text
        assert text.startswith(f"1{_DORIS_CSV_SEPARATOR}alice{_DORIS_CSV_SEPARATOR}")
        assert "2024-01-01 00:00:00" in text
    finally:
        out.unlink(missing_ok=True)


def test_csv_to_stream_load_temp_null_as_backslash_n(tmp_path):
    """Doris CSV 空值须为 \\N，空字符串会导致数值列整行过滤。"""
    from app.services.file_import_exec import _DORIS_CSV_SEPARATOR, _csv_to_stream_load_temp

    p = tmp_path / "nulls.csv"
    p.write_text("id,score,note\n1,,hello\n2,3.5,\n", encoding="utf-8")
    cols = [
        {"name": "id", "type": "bigint"},
        {"name": "score", "type": "double"},
        {"name": "note", "type": "string"},
    ]
    out, n = _csv_to_stream_load_temp(
        p, encoding="utf-8", delimiter=",", has_header=True, cols=cols, max_rows=1000
    )
    try:
        text = out.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert n == 2
        assert f"1{_DORIS_CSV_SEPARATOR}\\N{_DORIS_CSV_SEPARATOR}hello" in text
        assert f"2{_DORIS_CSV_SEPARATOR}3.5{_DORIS_CSV_SEPARATOR}\\N" in text
    finally:
        out.unlink(missing_ok=True)


def test_csv_to_stream_load_temp_keeps_json_commas_as_one_field(tmp_path):
    """JSON 内逗号不能被拆成多余列（ErrorURL: actual 19 vs schema 14）。"""
    from app.services.file_import_exec import _DORIS_CSV_SEPARATOR, _csv_to_stream_load_temp

    json_blob = '{"request": {"token": "abc", "currency": "USD"}, "response": {"status": "ACTIVE"}}'
    p = tmp_path / "auth.jsonish.csv"
    # 源文件仍是逗号 CSV，字段用 RFC4180 引号包裹
    p.write_text(
        'id,payload,flag\n1,"' + json_blob.replace('"', '""') + '",1\n',
        encoding="utf-8",
    )
    cols = [
        {"name": "id", "type": "bigint"},
        {"name": "payload", "type": "string"},
        {"name": "flag", "type": "bigint"},
    ]
    out, n = _csv_to_stream_load_temp(
        p, encoding="utf-8", delimiter=",", has_header=True, cols=cols, max_rows=1000
    )
    try:
        line = out.read_text(encoding="utf-8").strip().splitlines()[0]
        parts = line.split(_DORIS_CSV_SEPARATOR)
        assert n == 1
        assert len(parts) == 3
        assert parts[0] == "1"
        # SOH 分隔下整段 JSON 仍是一列；含 " 时 writer 会包一层引号并 "" 转义
        assert "currency" in parts[1] and "USD" in parts[1]
        assert parts[2] == "1"
    finally:
        out.unlink(missing_ok=True)


def test_doris_stream_load_data_quality_error_message(tmp_path):
    from app.services.file_import_exec import _doris_stream_load
    import pytest

    p = tmp_path / "data.csv"
    p.write_text("1,a\n", encoding="utf-8")
    cols = [{"name": "id", "type": "bigint"}, {"name": "name", "type": "string"}]
    ds = _Ds("doris", database="demo", port=9030)
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "Status": "Fail",
        "Message": "[DATA_QUALITY_ERROR]too many filtered rows",
        "NumberLoadedRows": 0,
        "NumberTotalRows": 100,
        "NumberFilteredRows": 90,
        "ErrorURL": "http://be:8040/api/_load_error_log?id=1",
    }
    with patch("app.services.file_import_exec.requests.put", return_value=fake):
        with pytest.raises(ValueError) as ei:
            _doris_stream_load(ds, "t1", cols, p)
    msg = str(ei.value)
    assert "too many filtered" in msg
    assert "filtered=90/100" in msg
    assert "ErrorURL=" in msg


def test_doris_stream_load_follows_307_with_auth(tmp_path):
    from app.services.file_import_exec import _doris_stream_load

    p = tmp_path / "data.csv"
    p.write_text("1,a\n", encoding="utf-8")
    cols = [{"name": "id", "type": "bigint"}, {"name": "name", "type": "string"}]
    ds = _Ds("doris", database="demo", port=9030, username="u1", password="p1")

    redirect = MagicMock()
    redirect.status_code = 307
    redirect.headers = {"Location": "http://be:8040/api/demo/t1/_stream_load"}

    ok = MagicMock()
    ok.status_code = 200
    ok.json.return_value = {
        "Status": "Success",
        "NumberLoadedRows": 1,
        "NumberTotalRows": 1,
        "NumberFilteredRows": 0,
    }

    with patch("app.services.file_import_exec.requests.put", side_effect=[redirect, ok]) as put:
        read, written = _doris_stream_load(ds, "t1", cols, p)
    assert read == 1 and written == 1
    assert put.call_count == 2
    assert put.call_args_list[0].kwargs["auth"] == ("u1", "p1")
    assert put.call_args_list[1].args[0] == "http://be:8040/api/demo/t1/_stream_load"
    assert put.call_args_list[1].kwargs["auth"] == ("u1", "p1")
    assert put.call_args_list[1].kwargs["allow_redirects"] is False


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
        client_key="ws9|demo.csv|26|1",
    )
    fid = init["file_id"]
    for i, p in enumerate(parts):
        save_upload_chunk(workspace_id=9, file_id=fid, chunk_index=i, content=p)
    meta = finalize_chunked_upload(workspace_id=9, file_id=fid)
    assert meta["status"] == "ready"
    assert resolve_data_path(meta).read_bytes() == raw


def test_chunked_upload_resume_and_idempotent(tmp_path, monkeypatch):
    from app.services.file_import_store import (
        init_chunked_upload,
        save_upload_chunk,
        get_upload_status,
    )

    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_UPLOAD_DIR", str(tmp_path))
    raw = b"abcdefghijKLMNOPQRST"
    parts = [raw[:10], raw[10:]]
    key = "resume-key-1"
    init1 = init_chunked_upload(
        workspace_id=1,
        user_id=1,
        filename="x.csv",
        size_bytes=len(raw),
        total_chunks=2,
        client_key=key,
    )
    fid = init1["file_id"]
    r1 = save_upload_chunk(workspace_id=1, file_id=fid, chunk_index=0, content=parts[0])
    assert r1["received"] == 1
    r1b = save_upload_chunk(workspace_id=1, file_id=fid, chunk_index=0, content=parts[0])
    assert r1b["skipped"] is True

    init2 = init_chunked_upload(
        workspace_id=1,
        user_id=1,
        filename="x.csv",
        size_bytes=len(raw),
        total_chunks=2,
        client_key=key,
    )
    assert init2["resumed"] is True
    assert init2["file_id"] == fid
    assert 0 in init2["received_chunks"]
    assert 1 in init2["missing_chunks"]

    st = get_upload_status(1, fid)
    assert st["received"] == 1
    assert st["missing_chunks"] == [1]


def test_chunked_upload_force_new(tmp_path, monkeypatch):
    from app.services.file_import_store import init_chunked_upload, save_upload_chunk

    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_UPLOAD_DIR", str(tmp_path))
    raw = b"0123456789abcdef"
    key = "force-new-key"
    init1 = init_chunked_upload(
        workspace_id=2,
        user_id=1,
        filename="y.csv",
        size_bytes=len(raw),
        total_chunks=2,
        client_key=key,
    )
    fid1 = init1["file_id"]
    save_upload_chunk(workspace_id=2, file_id=fid1, chunk_index=0, content=raw[:8])

    init2 = init_chunked_upload(
        workspace_id=2,
        user_id=1,
        filename="y.csv",
        size_bytes=len(raw),
        total_chunks=2,
        client_key=key,
        force_new=True,
    )
    assert init2["resumed"] is False
    assert init2["file_id"] != fid1
    assert init2["received"] == 0


def test_shared_chunk_path_uses_s3(tmp_path, monkeypatch):
    """多副本路径：meta/分片写入 S3（mock），本地仅作缓存。"""
    import shutil

    from app.services import file_import_store as store

    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(store, "file_import_shared_enabled", lambda: True)
    monkeypatch.setattr(
        store,
        "file_import_s3_uri",
        lambda ws, fid, fmt: f"s3://test-bucket/file-imports/{ws}/{fid}/data.{fmt}",
    )

    bucket: dict = {}

    def put_obj(ns, filename, content, content_type):
        bucket[f"{ns}/{filename}"] = content

    def get_obj(ns, filename):
        return bucket.get(f"{ns}/{filename}")

    def exists(ns, filename):
        return f"{ns}/{filename}" in bucket

    def list_names(ns, subprefix=""):
        prefix = f"{ns}/"
        sub = (subprefix or "").strip("/")
        want = prefix + (sub + "/" if sub else "")
        out = []
        for k in bucket:
            if k.startswith(want):
                out.append(k[len(prefix) :])
        return out

    def put_file(ns, filename, path, content_type="application/octet-stream"):
        bucket[f"{ns}/{filename}"] = Path(path).read_bytes()

    def download_to(ns, filename, dest):
        data = bucket.get(f"{ns}/{filename}")
        if data is None:
            return False
        Path(dest).write_bytes(data)
        return True

    def delete_prefix(ns, subprefix=""):
        names = list_names(ns, subprefix)
        for n in names:
            bucket.pop(f"{ns}/{n}", None)
        return len(names)

    monkeypatch.setattr(store, "put_shared_object", put_obj)
    monkeypatch.setattr(store, "get_shared_object", get_obj)
    monkeypatch.setattr(store, "list_shared_object_names", list_names)
    monkeypatch.setattr(store, "put_shared_object_file", put_file)
    monkeypatch.setattr(store, "download_shared_object_to_file", download_to)
    monkeypatch.setattr(store, "delete_shared_objects_with_prefix", delete_prefix)
    monkeypatch.setattr(store, "delete_shared_object", lambda ns, fn: bucket.pop(f"{ns}/{fn}", None))

    raw = b"id,name\n1,a\n2,b\n"
    parts = [raw[:6], raw[6:]]
    init = store.init_chunked_upload(
        workspace_id=3,
        user_id=1,
        filename="s3.csv",
        size_bytes=len(raw),
        total_chunks=2,
        client_key="s3-key",
    )
    fid = init["file_id"]
    assert any(k.endswith("/meta.json") for k in bucket)

    # 模拟另一副本：只清本地目录，依赖 S3 meta
    local_folder = tmp_path / "3" / fid
    if local_folder.exists():
        shutil.rmtree(local_folder)

    store.save_upload_chunk(workspace_id=3, file_id=fid, chunk_index=0, content=parts[0])
    store.save_upload_chunk(workspace_id=3, file_id=fid, chunk_index=1, content=parts[1])

    if local_folder.exists():
        shutil.rmtree(local_folder)

    meta = store.finalize_chunked_upload(workspace_id=3, file_id=fid)
    assert meta["status"] == "ready"
    assert meta["storage"] == "s3"
    assert meta["s3_uri"] == f"s3://test-bucket/file-imports/3/{fid}/data.csv"
    assert store.resolve_data_path(meta).read_bytes() == raw
    assert f"file-imports/3/{fid}/data.csv" in bucket

    pub = store.file_import_storage_public(meta)
    assert pub["load_mode"] == "internal_table"
    assert pub["s3_uri"] == meta["s3_uri"]
    assert "S3(" in (pub.get("advanced_s3_tvf_hint") or "")


def test_reconcile_unions_redis_and_s3(tmp_path, monkeypatch):
    """Redis 漏记时仍以 S3 为准，避免 complete 误报缺片。"""
    from app.services import file_import_store as store

    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(store, "file_import_shared_enabled", lambda: True)
    monkeypatch.setattr(store, "_list_parts_redis", lambda ws, fid, total: [0])  # 漏了 1
    monkeypatch.setattr(store, "_reconcile_received_s3", lambda ws, fid, total: [0, 1])
    monkeypatch.setattr(store, "_reconcile_received_local", lambda folder, meta: [])

    meta = {"total_chunks": 2, "storage": "s3", "received_chunks": []}
    got = store._reconcile_received(9, "a" * 32, meta)
    assert got == [0, 1]
