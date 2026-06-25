# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from app.services.scheduler_engine.dolphin import dolphin_scheduler_engine


def get_scheduler_engine(name: str = "dolphin"):
    engine = (name or "dolphin").strip().lower()
    if engine == "dolphin":
        return dolphin_scheduler_engine
    raise ValueError(f"暂不支持调度引擎: {name}")
