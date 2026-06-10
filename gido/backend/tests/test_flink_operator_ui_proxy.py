# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.flink_operator_ui_proxy import _rewrite_html


def test_rewrite_html_base_href_not_doubled():
    raw = b"""<!DOCTYPE html><html><head>
    <base href="./">
    <link rel="stylesheet" href="styles.abc.css">
    <link rel="shortcut icon" href="/assets/favicon.ico">
    </head><body></body></html>"""
    out = _rewrite_html(raw, "/api/streaming/jobs/2/flink-ui").decode()
    assert '<base href="/api/streaming/jobs/2/flink-ui/">' in out
    assert "/flink-ui/api/streaming" not in out
    assert 'href="/api/streaming/jobs/2/flink-ui/assets/favicon.ico"' in out
