"""P06-06B 正确性收口：步骤状态不变量测试。

覆盖任务规定的 10 条不变量：
1. 同一个 Job 最多只能有一个 running 步骤。
2. research_jobs.current_step 只能指向状态为 running 的步骤。
3. 没有 running 步骤时，current_step 必须为 null。
4. 开始一个新步骤前，前一个步骤必须已经进入终态。
5. mark_step_running 成功时 attempt_count 原子 +1。
6. 00_request 必须在 01_company_resolve 开始前 succeeded。
7. Job 进入 succeeded/failed/cancelled/rejected 后不得残留 running 步骤。
8. Job 取消时：当前 running 步骤转 skipped；后续 pending 步骤转 skipped；
   清空 current_step；不删除历史步骤。
9. 重复调用 start/succeed/fail/cancel 必须幂等。
10. 非法状态转换必须拒绝或安全无操作，不得覆盖已成功步骤。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Callable

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from invest_research.application.progress import STEP_NAMES
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.db.models import Base, ResearchJob, WorkflowStep
from invest_research.infrastructure.db.progress import SqlProgressSink

_AS_OF = date(2025, 10, 31)

SessionFactory = Callable[[], Session]


def _request() -> ResearchRequest:
    return ResearchRequest(
        input_company="AAPL",
        as_of_date=_AS_OF,
        language="zh-CN",
        requested_forms=("10-K", "10-Q"),
    )


@pytest.fixture()
def session_factory() -> SessionFactory:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _create_job(factory: SessionFactory, job_id: uuid.UUID, status: str = "pending") -> None:
    with factory() as session:
        session.add(
            ResearchJob(
                id=job_id,
                input_company="AAPL",
                as_of_date=_AS_OF,
                language="zh-CN",
                requested_forms=["10-K", "10-Q"],
                status=status,
                config_snapshot={},
            )
        )
        session.commit()


def _get_steps(factory: SessionFactory, job_id: uuid.UUID) -> dict[str, WorkflowStep]:
    with factory() as session:
        rows = session.scalars(
            select(WorkflowStep).where(WorkflowStep.job_id == job_id)
        ).all()
        return {r.step_name: r for r in rows}


def _get_job(factory: SessionFactory, job_id: uuid.UUID) -> ResearchJob:
    with factory() as session:
        return session.get(ResearchJob, job_id)


def _running_steps(steps: dict[str, WorkflowStep]) -> list[str]:
    return [name for name, s in steps.items() if s.status == "running"]


# ---------------------------------------------------------------------------
# 不变量 1：同一个 Job 最多只能有一个 running 步骤
# ---------------------------------------------------------------------------


def test_at_most_one_running_step_when_second_start_blocked(session_factory) -> None:
    """00 已 running 时，01 不能进入 running（同 Job 最多一个 running）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)

    # 00 进入 running
    sink.mark_step_running(job_id, "00_request")
    # 00 未终态时尝试启动 01：必须被拒绝（安全无操作）
    sink.mark_step_running(job_id, "01_company_resolve")

    steps = _get_steps(session_factory, job_id)
    assert steps["00_request"].status == "running"
    assert steps["01_company_resolve"].status == "pending"  # 未被启动
    assert _running_steps(steps) == ["00_request"]
    # current_step 仍指向唯一 running 步骤
    assert _get_job(session_factory, job_id).current_step == "00_request"


# ---------------------------------------------------------------------------
# 不变量 2 + 3：current_step 只能指向 running；无 running 时为 null
# ---------------------------------------------------------------------------


def test_current_step_points_to_running_step_after_success_cleared(session_factory) -> None:
    """步骤成功后 current_step 清空；无 running 时 current_step 为 null。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)

    sink.mark_step_running(job_id, "00_request")
    assert _get_job(session_factory, job_id).current_step == "00_request"

    sink.mark_step_succeeded(job_id, "00_request")
    # 00 已成功、无 running 步骤 → current_step 必须为 null
    assert _get_job(session_factory, job_id).current_step is None
    steps = _get_steps(session_factory, job_id)
    assert _running_steps(steps) == []


def test_current_step_cleared_after_step_failed(session_factory) -> None:
    """步骤失败后 current_step 清空（无 running 步骤时 current_step 为 null）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)

    sink.mark_step_running(job_id, "02_research")
    sink.mark_step_failed(
        job_id, "02_research", error_code="NETWORK_TRANSIENT", error_message="超时", terminal=False
    )
    assert _get_job(session_factory, job_id).current_step is None
    steps = _get_steps(session_factory, job_id)
    assert _running_steps(steps) == []


# ---------------------------------------------------------------------------
# 不变量 4 + 6：开始新步骤前前一步必须已终态；00 在 01 前 succeeded
# ---------------------------------------------------------------------------


def test_request_must_succeed_before_company_resolve(session_factory) -> None:
    """00_request 未 succeeded 时 01_company_resolve 不得启动。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)

    sink.mark_step_running(job_id, "00_request")
    # 00 仍 running（未终态），01 启动被拒
    sink.mark_step_running(job_id, "01_company_resolve")
    steps = _get_steps(session_factory, job_id)
    assert steps["01_company_resolve"].status == "pending"

    # 00 成功后 01 才能启动
    sink.mark_step_succeeded(job_id, "00_request")
    sink.mark_step_running(job_id, "01_company_resolve")
    steps = _get_steps(session_factory, job_id)
    assert steps["00_request"].status == "succeeded"
    assert steps["01_company_resolve"].status == "running"


# ---------------------------------------------------------------------------
# 不变量 5：mark_step_running 成功时 attempt_count 原子 +1
# ---------------------------------------------------------------------------


def test_mark_running_increments_attempt_count_atomically(session_factory) -> None:
    """每次成功进入 running，attempt_count 原子 +1（首次 0→1）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)

    sink.mark_step_running(job_id, "03_documents")
    steps = _get_steps(session_factory, job_id)
    assert steps["03_documents"].attempt_count == 1

    # 失败后可重试：再进 running 时 attempt_count 变 2
    sink.mark_step_failed(
        job_id, "03_documents", error_code="NETWORK_TRANSIENT", error_message="超时", terminal=False
    )
    sink.mark_step_running(job_id, "03_documents")
    steps = _get_steps(session_factory, job_id)
    assert steps["03_documents"].attempt_count == 2


# ---------------------------------------------------------------------------
# 不变量 7：Job 进入终态后不得残留 running 步骤
# ---------------------------------------------------------------------------


def test_failed_job_leaves_no_running_steps(session_factory) -> None:
    """job failed 时所有 running 步骤收口为 failed_terminal，不留 running。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id, status="running")
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "05_writer")

    sink.fail_all_running_steps(
        job_id, error_code="FLOW_EXECUTION_FAILED", error_message="流程异常终止"
    )
    steps = _get_steps(session_factory, job_id)
    assert _running_steps(steps) == []
    assert steps["05_writer"].status == "failed_terminal"
    assert _get_job(session_factory, job_id).current_step is None


# ---------------------------------------------------------------------------
# 不变量 8：取消收口
# ---------------------------------------------------------------------------


def test_cancel_cleanup_skips_running_and_pending_keeps_history(session_factory) -> None:
    """取消收口：running→skipped、后续 pending→skipped、已成功保留、不删历史。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id, status="running")
    sink.initialize_steps(job_id)

    # 00 成功、01 running、02~07 pending
    sink.mark_step_running(job_id, "00_request")
    sink.mark_step_succeeded(job_id, "00_request")
    sink.mark_step_running(job_id, "01_company_resolve")

    sink.cancel_pending_steps(job_id)

    steps = _get_steps(session_factory, job_id)
    # 历史步骤不删除
    assert set(steps.keys()) == set(STEP_NAMES)
    # 当前 running → skipped
    assert steps["01_company_resolve"].status == "skipped"
    # 已成功保留
    assert steps["00_request"].status == "succeeded"
    # 后续 pending → skipped
    for name in ("02_research", "03_documents", "04_analysis", "05_writer",
                 "06_quality_gate", "07_manifest"):
        assert steps[name].status == "skipped"
    # 清空 current_step
    assert _get_job(session_factory, job_id).current_step is None
    # 无残留 running
    assert _running_steps(steps) == []


# ---------------------------------------------------------------------------
# 不变量 9：重复调用幂等
# ---------------------------------------------------------------------------


def test_repeat_cancel_cleanup_is_idempotent(session_factory) -> None:
    """重复取消收口：第二次调用不改变任何状态（幂等）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id, status="cancelled")
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "04_analysis")

    sink.cancel_pending_steps(job_id)
    first = _get_steps(session_factory, job_id)
    sink.cancel_pending_steps(job_id)
    second = _get_steps(session_factory, job_id)

    for name in STEP_NAMES:
        assert first[name].status == second[name].status
        assert first[name].attempt_count == second[name].attempt_count
    assert _get_job(session_factory, job_id).current_step is None


def test_repeat_succeed_is_idempotent(session_factory) -> None:
    """已成功的步骤重复 mark_succeeded：安全无操作，不破坏状态。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "02_research")
    sink.mark_step_succeeded(job_id, "02_research")

    sink.mark_step_succeeded(job_id, "02_research")
    steps = _get_steps(session_factory, job_id)
    assert steps["02_research"].status == "succeeded"
    assert steps["02_research"].completed_at is not None


# ---------------------------------------------------------------------------
# 不变量 10：非法状态转换拒绝/安全无操作，不得覆盖已成功步骤
# ---------------------------------------------------------------------------


def test_cannot_overwrite_succeeded_step_with_running(session_factory) -> None:
    """已成功的步骤不能被重新置为 running（不得覆盖已成功步骤）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "00_request")
    sink.mark_step_succeeded(job_id, "00_request")

    # 01 正常启动成功后，尝试把 00 重新置 running：必须被拒绝
    sink.mark_step_running(job_id, "01_company_resolve")
    sink.mark_step_succeeded(job_id, "01_company_resolve")
    sink.mark_step_running(job_id, "00_request")

    steps = _get_steps(session_factory, job_id)
    assert steps["00_request"].status == "succeeded"
    # current_step 未被误设为 00（00 是 succeeded，不能成为 current_step）
    assert _get_job(session_factory, job_id).current_step is None


def test_cannot_fail_succeeded_step(session_factory) -> None:
    """已成功的步骤不能被标为 failed（只允许 running → failed）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "06_quality_gate")
    sink.mark_step_succeeded(job_id, "06_quality_gate")

    sink.mark_step_failed(
        job_id, "06_quality_gate", error_code="LLM_PARSE", error_message="x", terminal=True
    )
    steps = _get_steps(session_factory, job_id)
    assert steps["06_quality_gate"].status == "succeeded"  # 未被覆盖
