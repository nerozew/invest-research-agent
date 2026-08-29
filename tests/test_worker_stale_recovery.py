"""Worker 启动恢复的 ORM 生命周期回归。"""

from __future__ import annotations

import uuid
from datetime import date
from types import ModuleType

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from invest_research.application.progress import StepRecordError
from invest_research.domain.status import JobStatus
from invest_research.infrastructure.db.models import ResearchJob, WorkflowStep


@pytest.fixture
def recovery_env(
    monkeypatch: pytest.MonkeyPatch,
    sql_session_factory: sessionmaker[Session],
) -> tuple[ModuleType, sessionmaker[Session], list[str]]:
    # worker 导入时构建 Celery app 并尝试恢复；必须先隔离数据库、broker 和 tracing。
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("BROKER_URL", "memory://")
    monkeypatch.setenv("FLOW_MODE", "fake")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    from invest_research.infrastructure.observability import metrics_events
    from invest_research.infrastructure.queue import worker

    factory = sql_session_factory  # 与 Worker 一致：autoflush=False、expire_on_commit=True。
    recovery_metrics: list[str] = []
    monkeypatch.setattr(worker, "_build_session_factory", lambda: factory)
    monkeypatch.setattr(metrics_events, "count_stale_recovery", recovery_metrics.append)
    return worker, factory, recovery_metrics


def _add_job(factory: sessionmaker[Session], status: JobStatus) -> uuid.UUID:
    job_id = uuid.uuid4()
    with factory() as session:
        session.add(
            ResearchJob(
                id=job_id,
                input_company="Acme",
                as_of_date=date(2026, 8, 27),
                requested_forms=["10-K"],
                status=status.value,
                current_step="active" if status is JobStatus.RUNNING else None,
            )
        )
        if status is JobStatus.RUNNING:
            for sequence, (name, step_status) in enumerate(
                (("finished", "succeeded"), ("active", "running")), start=1
            ):
                session.add(
                    WorkflowStep(
                        job_id=job_id,
                        step_name=name,
                        sequence_no=sequence,
                        status=step_status,
                    )
                )
        session.commit()
    return job_id


def test_stale_recovery_uses_ids_after_commit(
    recovery_env: tuple[ModuleType, sessionmaker[Session], list[str]],
) -> None:
    """真实任务和步骤均落库收口，提交过期不会中断多个任务的恢复。"""
    worker, factory, metrics = recovery_env
    stale_ids = {_add_job(factory, JobStatus.RUNNING) for _ in range(2)}
    success_id = _add_job(factory, JobStatus.SUCCEEDED)
    pending_id = _add_job(factory, JobStatus.PENDING)

    worker._run_stale_job_recovery()

    with factory() as session:
        jobs = {job.id: job for job in session.execute(select(ResearchJob)).scalars()}
        for job_id in stale_ids:
            row = jobs[job_id]
            assert row.status == JobStatus.FAILED.value
            assert row.error_code == "STALE_RUNNING_RECOVERED"
            assert row.failure_stage == "startup_recovery"
            assert row.current_step is None
            assert row.completed_at is not None
        assert jobs[success_id].status == JobStatus.SUCCEEDED.value
        assert jobs[pending_id].status == JobStatus.PENDING.value
        steps = session.execute(select(WorkflowStep)).scalars().all()
        assert len(steps) == 4
        for step in steps:
            if step.step_name == "active":
                assert step.status == "failed_terminal"
                assert step.error_json["error_code"] == "STALE_RUNNING_RECOVERED"
                assert step.completed_at is not None
            else:
                assert step.status == "succeeded"
                assert step.error_json == {}
    assert metrics == ["recovered"]

    worker._run_stale_job_recovery()
    assert metrics == ["recovered", "none"]


def test_step_recovery_failure_warns_with_id_and_continues_next_job(
    recovery_env: tuple[ModuleType, sessionmaker[Session], list[str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker, factory, metrics = recovery_env
    job_ids = {_add_job(factory, JobStatus.RUNNING) for _ in range(2)}
    attempted: list[uuid.UUID] = []

    class FailingFirstProgress(worker.SqlProgressSink):
        def fail_all_running_steps(
            self, job_id: uuid.UUID, *, error_code: str, error_message: str
        ) -> None:
            attempted.append(job_id)
            if len(attempted) == 1:
                raise StepRecordError("injected step failure")
            super().fail_all_running_steps(
                job_id, error_code=error_code, error_message=error_message
            )

    monkeypatch.setattr(worker, "SqlProgressSink", FailingFirstProgress)

    worker._run_stale_job_recovery()

    assert set(attempted) == job_ids
    assert "启动恢复收口步骤失败" in caplog.text
    assert str(attempted[0]) in caplog.text
    assert "启动 stale running Job 恢复失败" not in caplog.text
    assert "DetachedInstanceError" not in caplog.text
    assert metrics == ["recovered"]
    with factory() as session:
        step = session.execute(
            select(WorkflowStep).where(
                WorkflowStep.job_id == attempted[1], WorkflowStep.step_name == "active"
            )
        ).scalar_one()
        assert step.status == "failed_terminal"


def test_database_recovery_failure_does_not_block_startup(
    recovery_env: tuple[ModuleType, sessionmaker[Session], list[str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker, _, metrics = recovery_env

    def unavailable_factory() -> None:
        raise RuntimeError("injected database unavailable")

    monkeypatch.setattr(worker, "_build_session_factory", unavailable_factory)

    worker._run_stale_job_recovery()

    assert "启动 stale running Job 恢复失败（继续启动）" in caplog.text
    assert metrics == []
