# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""GIDO Batch PYTHON 节点运行时。

用法（Studio / Dolphin 回调共用）::

    from gido_job import job

    job.writelog("start")
    # 源码里也可写 "${LARK_WEBHOOK_URL}"（跑前与 SQL 一样展开全局变量）
    webhook = job.var("LARK_WEBHOOK_URL", default="")
    rows = job.execute("SELECT * FROM t WHERE dt='$[yyyy-MM-dd-1]'")
    job.writelog(f"rows={len(rows)}")

- 脚本正文与 SQL 一致：跑前展开 ``${key}`` / ``$[yyyy-MM-dd-1]``
- ``execute`` 内 SQL 再展开一轮宏（与 SQL 节点对齐）
- ``job.var("key")`` 读取同一份空间全局变量（适合密钥 / 特殊字符）
"""

from gido_job.job import GidoJob, job

__all__ = ["GidoJob", "job"]
