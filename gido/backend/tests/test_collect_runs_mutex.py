# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
采集必须全局互斥。

线上事故：五个后端进程同时插入同一个 `scheduler_run_key`，全部卡在
`wait_event = transactionid` 上，CPU 打满。`scheduler_run_key` 上有唯一索引，
并发插同一个键时，后插入的一方必须等先插入那个事务提交完才知道算不算冲突；
而先插入那一方的事务里还夹着对引擎的 HTTP 调用，于是等待方一路堆积。

锁原先只加在后台采集任务上，但 `collect_runs` 是公开函数，
API 里的手动采集与页面兜底采集都是直接调它的——等于没锁。
所以锁必须在 `collect_runs` 内部，调用方想绕都绕不过去。
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-collect-mutex")

import threading
import time

import pytest

from app.services import run_collector


@pytest.fixture(autouse=True)
def _reset_local_locks():
    """进程内锁是模块级的，用例之间要清掉，免得互相影响。"""
    from app.services import distributed_lock

    with distributed_lock._local_guard:
        distributed_lock._local_locks.clear()
    yield
    with distributed_lock._local_guard:
        distributed_lock._local_locks.clear()


def test_second_caller_skips_instead_of_piling_up(monkeypatch):
    """第一轮还在跑时，第二个调用方应立刻跳过，而不是排队等着一起写库。"""
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def _slow_pass(db, *, workspace_id=None, page_size=100):
        calls.append("ran")
        started.set()
        release.wait(timeout=5)
        return {"collected": True, "engine": "dolphin", "ingested": 1}

    monkeypatch.setattr(run_collector, "_collect_runs_unlocked", _slow_pass)

    holder_result: dict = {}

    def _hold():
        holder_result["out"] = run_collector.collect_runs(None)

    t = threading.Thread(target=_hold, daemon=True)
    t.start()
    assert started.wait(timeout=5), "第一轮采集没能启动"

    # 第一轮仍在进行中，这一次必须立刻返回而不是阻塞
    began = time.monotonic()
    out = run_collector.collect_runs(None)
    elapsed = time.monotonic() - began

    assert out["collected"] is False
    assert out["reason"] == "already_running"
    assert elapsed < 1.0, f"第二个调用方阻塞了 {elapsed:.2f}s，说明没有立刻跳过"
    assert calls == ["ran"], "同一时刻只允许一轮真正执行"

    release.set()
    t.join(timeout=5)
    assert holder_result["out"]["collected"] is True


def test_many_concurrent_callers_run_exactly_one_pass(monkeypatch):
    """复现事故形态：多个调用方几乎同时进来，只能有一轮真正写库。"""
    ran = []
    ran_lock = threading.Lock()
    gate = threading.Event()

    def _pass(db, *, workspace_id=None, page_size=100):
        with ran_lock:
            ran.append(1)
        # 停一下，模拟采集里那些引擎 HTTP 调用，让并发窗口真实存在
        gate.wait(timeout=2)
        return {"collected": True, "engine": "dolphin"}

    monkeypatch.setattr(run_collector, "_collect_runs_unlocked", _pass)

    results: list[dict] = []
    results_lock = threading.Lock()

    def _worker():
        out = run_collector.collect_runs(None)
        with results_lock:
            results.append(out)

    threads = [threading.Thread(target=_worker, daemon=True) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(timeout=5)

    assert len(results) == 5
    assert len(ran) == 1, f"有 {len(ran)} 轮采集并发跑起来了，锁没生效"
    collected = [r for r in results if r.get("collected")]
    skipped = [r for r in results if r.get("reason") == "already_running"]
    assert len(collected) == 1
    assert len(skipped) == 4


def test_lock_is_released_even_if_the_pass_blows_up(monkeypatch):
    """采集抛异常也必须放锁，否则下一轮永远拿不到锁，采集就彻底停了。"""
    def _boom(db, *, workspace_id=None, page_size=100):
        raise RuntimeError("引擎连不上")

    monkeypatch.setattr(run_collector, "_collect_runs_unlocked", _boom)
    with pytest.raises(RuntimeError):
        run_collector.collect_runs(None)

    # 锁已放开：换一个能成功的实现，应该照常跑
    monkeypatch.setattr(
        run_collector, "_collect_runs_unlocked",
        lambda db, **kw: {"collected": True, "engine": "dolphin"},
    )
    assert run_collector.collect_runs(None)["collected"] is True
