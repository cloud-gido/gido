"""回归：静默草稿不得写版本历史；显式保存才快照。"""
from __future__ import annotations


def _should_append_node_history(create_history: bool, old_script: str, new_script: str) -> bool:
    """与 studio.update_node 中 NodeHistory 条件保持一致。"""
    if not create_history:
        return False
    old_script = old_script or ""
    new_script = new_script or ""
    return bool(old_script and old_script != new_script)


def _should_append_stream_history(create_history: bool, patch_keys: set[str], changed: bool) -> bool:
    """与 streaming.update_job 中历史条件对齐（watch 字段有变更时）。"""
    watch = {
        "script_content",
        "main_class",
        "program_args",
        "parallelism",
        "streaming_properties",
        "flink_sql_submit_mode",
        "flink_jar_submit_mode",
        "flink_session_profile_id",
    }
    return bool(create_history and (watch & patch_keys) and changed)


def test_studio_silent_draft_never_writes_history():
    assert _should_append_node_history(False, "select 1", "select 2") is False


def test_studio_version_save_writes_history_when_script_changes():
    assert _should_append_node_history(True, "select 1", "select 2") is True


def test_studio_version_save_skips_history_when_unchanged():
    assert _should_append_node_history(True, "select 1", "select 1") is False


def test_studio_version_save_skips_history_when_old_empty():
    # 与现网一致：旧脚本为空时不落历史
    assert _should_append_node_history(True, "", "select 1") is False


def test_stream_silent_draft_never_writes_history():
    assert _should_append_stream_history(False, {"script_content"}, True) is False


def test_stream_version_save_writes_history_on_script_change():
    assert _should_append_stream_history(True, {"script_content"}, True) is True


def test_stream_version_save_skips_when_not_changed():
    assert _should_append_stream_history(True, {"script_content"}, False) is False
