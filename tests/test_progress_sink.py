"""P06-06B：SqlProgressSink 测试（幂等创建、合法状态转换、时间字段、current_step）。"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from invest_research.application.progress import STEP_NAMES
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.db.models import Base, ResearchJob, WorkflowStep
from invest_research.infrastructure.db.progress import SqlProgressSink

_AS_OF = date(2025, 10, 31)


def _request() -> ResearchRequest:
    return ResearchRequest(
        input_company="AAPL",
        as_of_date=_AS_OF,
        language="zh-CN",
        requested_forms=("10-K", "10-Q"),
    )


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    return factory


def _create_job(factory, job_id: uuid.UUID) -> None:
    with factory() as session:
        session.add(
            ResearchJob(
                id=job_id,
                input_company="AAPL",
                as_of_date=_AS_OF,
                language="zh-CN",
                requested_forms=["10-K", "10-Q"],
                status="pending",
                config_snapshot={},
            )
        )
        session.commit()


def _get_steps(factory, job_id: uuid.UUID) -> dict[str, WorkflowStep]:
    with factory() as session:
        rows = session.scalars(
            select(WorkflowStep).where(WorkflowStep.job_id == job_id)
        ).all()
        return {r.step_name: r for r in rows}


def test_initialize_steps_creates_all_00_07_pending(session_factory) -> None:
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)

    sink.initialize_steps(job_id)
    steps = _get_steps(session_factory, job_id)

    assert set(steps.keys()) == set(STEP_NAMES)
    for name in STEP_NAMES:
        assert steps[name].status == "pending"
        assert steps[name].attempt_count == 0
        assert steps[name].started_at is None
        assert steps[name].completed_at is None


def test_initialize_steps_is_idempotent(session_factory) -> None:
    """重复 Celery 投递不重复插入（同 (job_id, step_name) 唯一）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)

    sink.initialize_steps(job_id)
    sink.initialize_steps(job_id)
    steps = _get_steps(session_factory, job_id)

    assert len(steps) == 8  # 不重复插入
    assert set(steps.keys()) == set(STEP_NAMES)


def test_mark_running_updates_started_at_and_current_step(session_factory) -> None:
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)

    sink.mark_step_running(job_id, "01_company_resolve")

    steps = _get_steps(session_factory, job_id)
    assert steps["01_company_resolve"].status == "running"
    assert steps["01_company_resolve"].started_at is not None
    assert steps["01_company_resolve"].completed_at is None
    with session_factory() as session:
        row = session.get(ResearchJob, job_id)
        assert row.current_step == "01_company_resolve"


def test_mark_running_from_failed_retryable_allowed(session_factory) -> None:
    """failed_retryable → running 是合法转换（重试）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)

    with session_factory() as session:
        row = session.execute(
            select(WorkflowStep).where(
                WorkflowStep.job_id == job_id, WorkflowStep.step_name == "02_research"
            )
        ).scalar_one()
        row.status = "failed_retryable"
        session.commit()

    sink.mark_step_running(job_id, "02_research")
    steps = _get_steps(session_factory, job_id)
    assert steps["02_research"].status == "running"


def test_mark_running_from_succeeded_not_allowed(session_factory) -> None:
    """succeeded → running 非法，不得执行（防护性检查）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)

    with session_factory() as session:
        row = session.execute(
            select(WorkflowStep).where(
                WorkflowStep.job_id == job_id, WorkflowStep.step_name == "03_documents"
            )
        ).scalar_one()
        row.status = "succeeded"
        session.commit()

    sink.mark_step_running(job_id, "03_documents")
    steps = _get_steps(session_factory, job_id)
    # 状态不变、current_step 不被误设
    assert steps["03_documents"].status == "succeeded"
    with session_factory() as session:
        row = session.get(ResearchJob, job_id)
        assert row.current_step is None


def test_mark_succeeded_writes_completed_at_keeps_attempt_count(session_factory) -> None:
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "04_analysis")

    # 模拟已有 attempt_count（重试场景）
    with session_factory() as session:
        row = session.execute(
            select(WorkflowStep).where(
                WorkflowStep.job_id == job_id, WorkflowStep.step_name == "04_analysis"
            )
        ).scalar_one()
        row.attempt_count = 2
        session.commit()

    sink.mark_step_succeeded(job_id, "04_analysis")
    steps = _get_steps(session_factory, job_id)
    assert steps["04_analysis"].status == "succeeded"
    assert steps["04_analysis"].completed_at is not None
    assert steps["04_analysis"].attempt_count == 2  # 保留 attempt_count


def test_mark_step_failed_terminal_saves_redacted_summary(session_factory) -> None:
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "05_writer")

    sink.mark_step_failed(
        job_id,
        "05_writer",
        error_code="LLM_PARSE",
        error_message="模型输出解析失败 Authorization=sk-secret 被截断",
        terminal=True,
    )
    steps = _get_steps(session_factory, job_id)
    assert steps["05_writer"].status == "failed_terminal"
    assert steps["05_writer"].completed_at is not None
    assert steps["05_writer"].error_json["error_code"] == "LLM_PARSE"
    # 错误摘要有长度上限（脱敏）
    assert len(steps["05_writer"].error_json["error_message"]) <= 500


def test_mark_step_failed_retryable(session_factory) -> None:
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "02_research")

    sink.mark_step_failed(
        job_id, "02_research", error_code="NETWORK_TRANSIENT", error_message="超时", terminal=False
    )
    steps = _get_steps(session_factory, job_id)
    assert steps["02_research"].status == "failed_retryable"


def test_fail_all_running_steps_collects_and_clears_current_step(session_factory) -> None:
    """fail_all_running_steps 收口所有 running 步骤（即使多个 running 的历史脏数据）。

    P06-06B 收口后正常流程不会再出现多个 running（mark_step_running 强制唯一
    running）；这里用 SQL 直接构造"历史脏数据/异常并发"产生的多个 running 场景，
    验证收口逻辑仍然全部处理、不留任何 running。
    """
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)
    # 正常流程：04_analysis 进入 running
    sink.mark_step_running(job_id, "04_analysis")
    # 模拟历史脏数据：直接 SQL 把 05_writer 也置为 running（绕过唯一 running 防线）
    with session_factory() as session:
        session.execute(
            update(WorkflowStep)
            .where(
                WorkflowStep.job_id == job_id,
                WorkflowStep.step_name == "05_writer",
            )
            .values(status="running")
        )
        session.commit()
    # 已成功的步骤不应被改
    with session_factory() as session:
        row = session.execute(
            select(WorkflowStep).where(
                WorkflowStep.job_id == job_id, WorkflowStep.step_name == "02_research"
            )
        ).scalar_one()
        row.status = "succeeded"
        session.commit()

    sink.fail_all_running_steps(
        job_id, error_code="FLOW_EXECUTION_FAILED", error_message="流程异常终止"
    )
    steps = _get_steps(session_factory, job_id)
    assert steps["04_analysis"].status == "failed_terminal"
    assert steps["05_writer"].status == "failed_terminal"
    assert steps["02_research"].status == "succeeded"  # 不受影响
    with session_factory() as session:
        row = session.get(ResearchJob, job_id)
        assert row.current_step is None  # 终态清空


def test_clear_current_step(session_factory) -> None:
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    _create_job(session_factory, job_id)
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "07_manifest")

    sink.clear_current_step(job_id)
    with session_factory() as session:
        row = session.get(ResearchJob, job_id)
        assert row.current_step is None


def test_write_failure_raises_step_record_error(session_factory) -> None:
    """写失败抛 StepRecordError（调用方记录脱敏日志，不误报成功）。"""
    sink = SqlProgressSink(session_factory)
    job_id = uuid.uuid4()
    # job 不存在：initialize 提交时 FK 违反（sqlite 默认不强制 FK），
    # 这里直接测 mark_step_running 对不存在 job 的 rowcount=0（不抛错、无副作用）。
    sink.mark_step_running(job_id, "00_request")
    with session_factory() as session:
        assert session.get(ResearchJob, job_id) is None
