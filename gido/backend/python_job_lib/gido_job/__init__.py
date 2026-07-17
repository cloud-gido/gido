# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""GIDO Batch PYTHON 节点运行时。

用法（Studio / Dolphin 回调共用）::

    from gido_job import job

    job.writelog("start")
    rows = job.execute("SELECT * FROM t WHERE dt='$[yyyy-MM-dd-1]'")
    job.writelog(f"rows={len(rows)}")

``execute`` 与 SQL 节点一致，自动展开 ``$[yyyy-MM-dd-1]`` / ``${bizdate}`` 等宏。
"""

from gido_job.job import GidoJob, job

__all__ = ["GidoJob", "job"]
