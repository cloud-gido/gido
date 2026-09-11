# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
往带唯一约束的表里安全插入。

不要写「先查再插」：两个并发请求都会查到「不存在」，然后都去插。
在 Postgres 上后插入那一方不会立刻报错，而是**阻塞等先插入的事务提交**
（`wait_event = transactionid`），因为在那之前无法判定算不算冲突。
如果先插入那个事务里还夹着网络调用，等待方就会一路堆积，把连接池吃干、CPU 打满
——GIDO 线上就因为 `dw_workflow_instances.scheduler_run_key` 这么炸过一次。

正确做法是让数据库裁决：插进去，撞了唯一约束再退回已存在那行。
savepoint 是必需的——IntegrityError 会让整个事务进入不可用状态，
没有 savepoint 的话同一个 session 后续所有操作都会跟着失败。
"""
from __future__ import annotations

from typing import Any, Callable, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def insert_or_get(
    db: Session, instance: Any, find_existing: Callable[[], Any]
) -> Tuple[Any, bool]:
    """
    插入 `instance`；撞唯一约束就用 `find_existing()` 取回已存在那行。

    返回 `(行, 是否本次新建)`。`find_existing()` 仍取不到就把原异常抛出去——
    那说明冲突不是来自我们预期的那个唯一键，不能一声不响地当成幂等。
    """
    savepoint = db.begin_nested()
    try:
        db.add(instance)
        db.flush()
        savepoint.commit()
        return instance, True
    except IntegrityError:
        savepoint.rollback()
        existing = find_existing()
        if existing is None:
            raise
        return existing, False
