# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
并发写安全：带唯一约束的表不许「先查再插」，入队这类操作必须在被调方互斥。

这些用例锁住的是一类生产事故：两个并发请求都查到「不存在」，然后都去插，
在 Postgres 上后写的一方会阻塞等前一个事务提交，请求连着 DB 会话一起堆积，
把连接池吃干、把无关业务一起拖死。

注意这里锁住的是**设计属性**（不先查、撞了能恢复、入队互斥），不是并发时序本身：
Postgres 那个 `wait_event = transactionid` 的阻塞行为要真起并发打真库才能复现，
单线程 sqlite 用例做不到，别把这组用例当成「并发已验证」。
"""
import threading

import pytest
from sqlalchemy import Column, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from app.services.db_idempotent import insert_or_get

_Base = declarative_base()


class _Thing(_Base):
    __tablename__ = "t_things"
    id = Column(Integer, primary_key=True)
    slug = Column(String(64))
    note = Column(String(64))
    __table_args__ = (UniqueConstraint("slug", name="uq_thing_slug"),)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()


def test_it_does_not_precheck_but_lets_the_database_decide(session):
    """
    无冲突时压根不该查一次。

    这条是这组用例里真正能区分两种实现的：「先查再插」必然要先查一次，
    而竞态的根源就是那次查——查完到插进去之间，别人可以把行插进来。
    """
    probes = []

    def _find():
        probes.append(1)
        return session.query(_Thing).filter(_Thing.slug == "a").first()

    row, created = insert_or_get(session, _Thing(slug="a", note="first"), _find)
    assert created is True
    assert row.note == "first"
    assert probes == [], "插入成功的路径上不该有预检查查询"


def test_losing_a_race_returns_the_winner_row_instead_of_raising(session):
    session.add(_Thing(slug="a", note="first"))
    session.commit()

    row, created = insert_or_get(
        session,
        _Thing(slug="a", note="second"),
        lambda: session.query(_Thing).filter(_Thing.slug == "a").first(),
    )
    assert created is False
    # 拿回的是先到那一行，不是我们想插的那行
    assert row.note == "first"
    assert session.query(_Thing).count() == 1


def test_session_stays_usable_after_losing_a_race(session):
    """
    撞约束之后 session 还得能用。

    IntegrityError 会让整个事务进入不可用状态，没有 savepoint 兜着的话，
    同一个请求里后续所有写操作都会跟着失败——一次幂等冲突会放大成整个请求报废。
    """
    session.add(_Thing(slug="a", note="first"))
    session.commit()

    insert_or_get(
        session,
        _Thing(slug="a", note="second"),
        lambda: session.query(_Thing).filter(_Thing.slug == "a").first(),
    )

    session.add(_Thing(slug="b", note="other"))
    session.commit()
    assert session.query(_Thing).filter(_Thing.slug == "b").first() is not None


def test_unexpected_conflict_is_not_swallowed(session):
    """冲突不是来自我们预期的唯一键时必须抛出，不能一声不响地当成幂等。"""
    session.add(_Thing(slug="a", note="first"))
    session.commit()

    with pytest.raises(IntegrityError):
        insert_or_get(session, _Thing(slug="a", note="second"), lambda: None)


@pytest.mark.parametrize("url", ["postgresql+psycopg2://u:p@h/db", "mysql+pymysql://u:p@h/db"])
def test_pool_always_has_an_acquisition_timeout(url):
    """
    池必须有 pool_timeout。

    没有它，池满之后请求是**无限期**等连接，一个慢查询就能让无关接口一起挂到
    网关超时。有它则是快速失败，故障被限制在拿不到连接的那几个请求上。
    """
    from app.core.database import build_engine_kwargs

    kwargs = build_engine_kwargs(url)
    assert kwargs.get("pool_timeout"), "生产库必须配 pool_timeout"
    assert kwargs.get("pool_size"), "不要吃 SQLAlchemy 默认池大小"
    assert kwargs.get("pool_pre_ping") is True


def test_redis_outage_does_not_stop_scheduled_work(monkeypatch):
    """
    Redis 挂了不能让定时任务集体停摆。

    生产 SHARED_STATE_REQUIRED=true，`claim_once` 在 Redis 不可用时是抛异常的。
    后台任务直接调它、又没兜异常的话，Redis 一抖，定时工作流、实例采集、告警重投、
    基线巡检会全部不跑——而这些的正确性靠的是 Postgres advisory 锁，跟 Redis 无关。
    共享状态的故障不该扩散成核心功能的故障。
    """
    from app.services import shared_state

    def _boom(*_a, **_kw):
        raise RuntimeError("多副本共享状态已启用，禁止降级为进程内状态")

    monkeypatch.setattr(shared_state, "claim_once", _boom)
    assert shared_state.claim_or_proceed("whatever", 60) is True


@pytest.mark.parametrize(
    "claim_result, should_proceed",
    [(True, True), (None, True), (False, False)],
)
def test_only_an_explicit_other_replica_claim_skips_the_round(
    monkeypatch, claim_result, should_proceed
):
    """只有「明确被别的副本抢到」才跳过；没配 Redis（None）照常放行。"""
    from app.services import shared_state

    monkeypatch.setattr(shared_state, "claim_once", lambda *_a, **_kw: claim_result)
    assert shared_state.claim_or_proceed("whatever", 60) is should_proceed


def test_background_jobs_do_not_call_claim_once_directly():
    """
    定时任务必须走 claim_or_proceed。

    直接调 claim_once 就等于把 Redis 变成定时调度的硬依赖——这条是防回归的。
    """
    import inspect

    from app.services import scheduler

    src = inspect.getsource(scheduler)
    assert "claim_once" not in src, "scheduler 里不该直接用 claim_once，改用 claim_or_proceed"


def test_sync_enqueue_is_mutually_exclusive_in_the_callee(monkeypatch):
    """
    入队互斥必须在 enqueue_sync_record 内部。

    它有 8 个调用方（定时、手动、重跑、工作流节点……），守调用方只能守住
    你记得的那几个——线上采集那次事故就是这么来的。
    """
    from app.services import distributed_lock, sync_worker

    distributed_lock._local_locks.clear()

    started = threading.Event()
    ran = []
    rejected = []

    def _slow_enqueue(task_id, **kwargs):
        ran.append(task_id)
        started.set()
        threading.Event().wait(0.3)
        return object()

    monkeypatch.setattr(sync_worker, "_enqueue_sync_record_locked", _slow_enqueue)

    def _call():
        try:
            sync_worker.enqueue_sync_record(7)
        except RuntimeError:
            rejected.append(1)

    first = threading.Thread(target=_call)
    first.start()
    assert started.wait(2), "第一个入队没跑起来"

    second = threading.Thread(target=_call)
    second.start()
    second.join(2)
    first.join(2)

    # 第二个必须被立刻拒绝，而不是排队等着——排队本身就是故障形态
    assert len(ran) == 1
    assert len(rejected) == 1
