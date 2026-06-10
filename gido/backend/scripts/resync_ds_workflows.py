# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env python3
"""容器内批量再发布所有工作流到 DolphinScheduler（可不登录）。"""
import os
import sys

# 兼容本地直接执行与 Docker WORKDIR=/app
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

def main() -> int:
    from app.models import rbac_models  # noqa: F401 — 注册 User→Role，避免 Mapper 报错
    from app.core.database import SessionLocal
    from app.services.ds_runtime import get_dolphin_runtime
    from app.services.workflow_ds_publish import bulk_publish_all_to_ds

    wid = None
    if len(sys.argv) > 1 and sys.argv[1].strip().isdigit():
        wid = int(sys.argv[1])
    db = SessionLocal()
    try:
        if not get_dolphin_runtime(db).enabled:
            print("DolphinScheduler 未启用（系统管理或 DS_ENABLED），退出")
            return 1
        results = bulk_publish_all_to_ds(db, wid)
        for r in results:
            print(r)
        err = sum(1 for r in results if r.get("error"))
        return 1 if err else 0
    finally:
        db.close()

if __name__ == "__main__":
    raise SystemExit(main())
