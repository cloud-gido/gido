# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import subprocess

from app.services.dolphin import _ds_async_node_callback
from app.services.ds_callback_auth import (
    node_callback_signature,
    verify_node_callback_signature,
)


def _fake_curl(tmp_path, terminal_status: str) -> str:
    state = tmp_path / "poll-count"
    executable = tmp_path / "curl"
    executable.write_text(
        f"""#!/bin/sh
case "$*" in
  *"/runs")
    printf '42'
    ;;
  *"/poll?"*)
    count=0
    [ ! -f {state!s} ] || count="$(cat {state!s})"
    count=$((count + 1))
    printf '%s' "$count" > {state!s}
    if [ "$count" -eq 1 ]; then
      printf 'running\\n1\\nfirst log\\n'
    else
      printf '{terminal_status}\\n2\\nlast log\\n'
    fi
    ;;
  *"/cancel")
    printf 'cancelled'
    ;;
  *)
    printf 'unexpected curl args: %s\\n' "$*" >&2
    exit 9
    ;;
esac
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return str(tmp_path)


def test_async_callback_streams_logs_and_exits_success(tmp_path):
    script = _ds_async_node_callback(17)
    env = {
        **os.environ,
        "PATH": f"{_fake_curl(tmp_path, 'success')}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["/bin/sh", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "[GIDO] submitted run_id=42" in result.stdout
    assert "first log" in result.stdout
    assert "last log" in result.stdout


def test_async_callback_propagates_failed_status(tmp_path):
    script = _ds_async_node_callback(17)
    env = {
        **os.environ,
        "PATH": f"{_fake_curl(tmp_path, 'failed')}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["/bin/sh", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "last log" in result.stdout


def test_async_callback_uses_scoped_signature_and_does_not_need_worker_token():
    script = _ds_async_node_callback(17)
    lowered = script.lower()

    assert "/api/studio/internal/nodes/17/runs" in script
    assert "/poll?after_seq=$seq" in script
    assert "/cancel" in script
    assert "X-Gido-Callback-Signature" in script
    assert "callback_signature=v1=" in script
    assert "GIDO_INTERNAL_TOKEN" not in script
    assert "--max-time 30" in script
    assert "--max-time 3600" not in script
    assert "$[yyyy-MM-dd]" in script
    assert "python" not in lowered


def test_callback_signature_is_scoped_to_one_node():
    signature = node_callback_signature(17)

    assert verify_node_callback_signature(17, signature)
    assert not verify_node_callback_signature(18, signature)
