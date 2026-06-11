# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./pytest_gido_vars.db")

from app.services import workspace_variables as wv


def test_substitute_workspace_variables(monkeypatch):
    monkeypatch.setattr(
        wv,
        "load_workspace_variable_map",
        lambda db, ws_id, scope: {
            "kafka.bootstrap": "kafka:9092",
            **({"stream.only": "yes"} if scope == "stream" else {}),
        },
    )

    class FakeWs:
        timezone = "Asia/Shanghai"

    class FakeDb:
        def query(self, _):
            return self

        def filter(self, *_):
            return self

        def first(self):
            return FakeWs()

    db = FakeDb()
    sql = "WITH ('bootstrap' = '${kafka.bootstrap}')"
    out = wv.substitute_script_variables(db, 1, sql, "batch")
    assert "kafka:9092" in out
    assert "${kafka.bootstrap}" not in out

    out_stream = wv.substitute_script_variables(db, 1, "${stream.only}", "stream")
    assert out_stream == "yes"
