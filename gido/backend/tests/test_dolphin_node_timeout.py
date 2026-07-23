# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.dolphin import _node_timeout_minutes


def test_timeout_minutes_when_seconds_explicit_null():
    # 前端常存 timeout_seconds: null；dict.get 默认值不会生效
    assert _node_timeout_minutes({"timeout_seconds": None}) == 60


def test_timeout_minutes_default_and_custom():
    assert _node_timeout_minutes({}) == 60
    assert _node_timeout_minutes({"timeout_seconds": 7200}) == 120
    assert _node_timeout_minutes({"timeout_seconds": "900"}) == 15
