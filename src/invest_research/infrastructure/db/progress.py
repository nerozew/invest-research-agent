"""P06-06B：SqlProgressSink —— ProgressSink 端口的 SQL 实现。

- 每个操作独立短事务（每次新建 session），进度写入失败不影响任务本身；
- 幂等创建 00-07 步骤（重复 Celery 投递复用 ``uq_workflow_steps_job_step`` 唯一约束）；
- 合法状态转换防护：只更新允许的来源状态；
- 错误摘要脱敏：只保存错误码与脱敏错误消息（不含 key/token/内部路径/完整 prompt）；
- 写失败抛 ``StepRecordError``，由调用方记录脱敏日志后继续任务（不误报失败）。

依赖方向：infrastructure -> application（端口常量）+ db models。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from invest_research.application.progress import STEP_NAMES, STEP_SEQUENCE, StepRecordError
from invest_research.infrastructure.db.models import ResearchJob as ResearchJobORM
from invest_research.infrastructure.db.models import WorkflowStep as WorkflowStepORM
from invest_research.infrastructure.db.repositories import SessionFactory

# SQLAlchemy 2.0 的 session.execute(update(...)) 返回 CursorResult（mypy 类型收窄）。
# mypy 只看到 Result[Any] 时没有 rowcount；这里显式收窄到 CursorResult。
from typing import Any, cast

from sqlalchemy.engine import CursorResult

# 步骤开始/结束的合法来源状态（对齐 domain.status.StepTransitions 的应用子集）。
_STARTABLE_SOURCES = ("pending", "failed_retryable")
_SUCCEEDABLE_SOURCES = ("running",)
_FAILABLE_SOURCES = ("running",)

# 错误摘要最大长度（防止超长意外写入；错误消息在调用方已脱敏）。
_MAX_ERROR_MESSAGE = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SqlProgressSink:
    """ProgressSink 的 SQLAlchemy 实现（独立短事务、幂等创建、合法状态转换）。

    构造参数：``session_factory``（与 JobRepository 相同的 SessionFactory 类型）。
    本类不共享 session；每个方法独立 ``with self._sf() as session``。
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    def initialize_steps(self, job_id: uuid.UUID) -> None:
        """幂等创建 00-07 步骤（初始 pending、attempt_count=0）。

        重复投递时已存在的步骤不重复插入（依赖唯一约束 + 先查再插）。
        单条插入失败不删除其它步骤（下次投递继续补齐）。
        """
        with self._sf() as session:
            try:
                existing = set(
                    session.scalars(
                        select(WorkflowStepORM.step_name).where(
                            WorkflowStepORM.job_id == job_id
                        )
                    ).all()
                )
                for name in STEP_NAMES:
                    if name in existing:
                        continue
                    session.add(
                        WorkflowStepORM(
                            id=uuid.uuid4(),
                            job_id=job_id,
                            step_name=name,
                            sequence_no=STEP_SEQUENCE[name],
                            status="pending",
                            attempt_count=0,
                            input_json={},
                            output_json={},
                            error_json={},
                        )
                    )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise StepRecordError(f"初始化步骤失败 job={job_id}: {exc}") from exc

    def mark_step_running(self, job_id: uuid.UUID, step_name: str) -> None:
        """步骤开始：pending/failed_retryable → running，写 started_at + current_step。"""
        with self._sf() as session:
            try:
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(WorkflowStepORM)
                        .where(
                            WorkflowStepORM.job_id == job_id,
                            WorkflowStepORM.step_name == step_name,
                            WorkflowStepORM.status.in_(_STARTABLE_SOURCES),
                        )
                        .values(status="running", started_at=_now())
                    ),
                )
                # 只有步骤存在且来源合法才更新 current_step（避免旧步骤误设）
                if result.rowcount > 0:
                    session.execute(
                        update(ResearchJobORM)
                        .where(ResearchJobORM.id == job_id)
                        .values(current_step=step_name)
                    )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise StepRecordError(
                    f"标记步骤开始失败 job={job_id} step={step_name}: {exc}"
                ) from exc

    def mark_step_succeeded(self, job_id: uuid.UUID, step_name: str) -> None:
        """步骤成功：running → succeeded，写 completed_at（保留 attempt_count）。"""
        with self._sf() as session:
            try:
                session.execute(
                    update(WorkflowStepORM)
                    .where(
                        WorkflowStepORM.job_id == job_id,
                        WorkflowStepORM.step_name == step_name,
                        WorkflowStepORM.status.in_(_SUCCEEDABLE_SOURCES),
                    )
                    .values(status="succeeded", completed_at=_now())
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise StepRecordError(
                    f"标记步骤成功失败 job={job_id} step={step_name}: {exc}"
                ) from exc

    def mark_step_failed(
        self,
        job_id: uuid.UUID,
        step_name: str,
        *,
        error_code: str,
        error_message: str,
        terminal: bool,
    ) -> None:
        """步骤失败：running → failed_retryable/failed_terminal，保存脱敏错误摘要。"""
        sanitized = (error_message or "")[:_MAX_ERROR_MESSAGE]
        with self._sf() as session:
            try:
                session.execute(
                    update(WorkflowStepORM)
                    .where(
                        WorkflowStepORM.job_id == job_id,
                        WorkflowStepORM.step_name == step_name,
                        WorkflowStepORM.status.in_(_FAILABLE_SOURCES),
                    )
                    .values(
                        status="failed_terminal" if terminal else "failed_retryable",
                        completed_at=_now(),
                        error_json={
                            "error_code": error_code,
                            "error_message": sanitized,
                        },
                    )
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise StepRecordError(
                    f"标记步骤失败失败 job={job_id} step={step_name}: {exc}"
                ) from exc

    def fail_all_running_steps(
        self,
        job_id: uuid.UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        """Job 失败时把该 job 所有仍为 running 的步骤统一收口为 failed_terminal。

        保证任务失败后不留任何虚假的 running 步骤；必须只更新 running 来源。
        """
        sanitized = (error_message or "")[:_MAX_ERROR_MESSAGE]
        with self._sf() as session:
            try:
                session.execute(
                    update(WorkflowStepORM)
                    .where(
                        WorkflowStepORM.job_id == job_id,
                        WorkflowStepORM.status == "running",
                    )
                    .values(
                        status="failed_terminal",
                        completed_at=_now(),
                        error_json={
                            "error_code": error_code,
                            "error_message": sanitized,
                        },
                    )
                )
                session.execute(
                    update(ResearchJobORM)
                    .where(ResearchJobORM.id == job_id)
                    .values(current_step=None)
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise StepRecordError(
                    f"收口 running 步骤失败 job={job_id}: {exc}"
                ) from exc

    def clear_current_step(self, job_id: uuid.UUID) -> None:
        """Job 进入终态时清空 research_jobs.current_step。"""
        with self._sf() as session:
            try:
                session.execute(
                    update(ResearchJobORM)
                    .where(ResearchJobORM.id == job_id)
                    .values(current_step=None)
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise StepRecordError(f"清空 current_step 失败 job={job_id}: {exc}") from exc
