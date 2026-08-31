"""P06-06B：SqlProgressSink —— ProgressSink 端口的 SQL 实现。

- 每个操作独立短事务（每次新建 session），进度写入失败不影响任务本身；
- 幂等创建 00-07 步骤（重复 Celery 投递复用 ``uq_workflow_steps_job_step`` 唯一约束）；
- 合法状态转换防护：只更新允许的来源状态；
- P06-06B 正确性收口不变量：
  - 同一 Job 最多一个 running 步骤（``mark_step_running`` 带"无其它 running"条件）；
  - ``current_step`` 只指向 running 步骤；步骤进入终态（succeeded/failed）时清空；
  - ``mark_step_running`` 成功时 ``attempt_count`` 原子 +1；
  - 取消收口（``cancel_pending_steps``）：running→skipped、pending→skipped、清空 current_step；
- 错误摘要脱敏：只保存错误码与脱敏错误消息（不含 key/token/内部路径/完整 prompt）；
- 写失败抛 ``StepRecordError``，由调用方记录脱敏日志后继续任务（不误报失败）。

依赖方向：infrastructure -> application（端口常量）+ db models。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

# SQLAlchemy 2.0 的 session.execute(update(...)) 返回 CursorResult（mypy 类型收窄）。
# mypy 只看到 Result[Any] 时没有 rowcount；这里显式收窄到 CursorResult。
from typing import Any, cast

from sqlalchemy import exists, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError

from invest_research.application.progress import STEP_NAMES, STEP_SEQUENCE, StepRecordError
from invest_research.infrastructure.db.models import ResearchJob as ResearchJobORM
from invest_research.infrastructure.db.models import WorkflowStep as WorkflowStepORM
from invest_research.infrastructure.db.repositories import SessionFactory

# 步骤开始/结束的合法来源状态（对齐 domain.status.StepTransitions 的应用子集）。
_STARTABLE_SOURCES = ("pending", "failed_retryable")
_SUCCEEDABLE_SOURCES = ("running",)
_FAILABLE_SOURCES = ("running",)

# 错误摘要最大长度（防止超长意外写入；错误消息在调用方已脱敏）。
_MAX_ERROR_MESSAGE = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _other_running_exists(job_id: uuid.UUID, step_name: str) -> Any:
    """同 Job 是否存在其它 running 步骤（排除当前步骤）。

    用于 ``mark_step_running`` 的唯一 running 不变量：
    只有"当前 Job 没有其它 running 步骤"时才允许新步骤进入 running。
    """
    return exists(
        select(WorkflowStepORM.id).where(
            WorkflowStepORM.job_id == job_id,
            WorkflowStepORM.step_name != step_name,
            WorkflowStepORM.status == "running",
        )
    )


class SqlProgressSink:
    """ProgressSink 的 SQLAlchemy 实现（独立短事务、幂等创建、合法状态转换）。"""

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
                        select(WorkflowStepORM.step_name).where(WorkflowStepORM.job_id == job_id)
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
        """步骤开始：pending/failed_retryable → running，写 started_at + current_step。

        不变量（P06-06B 收口）：
        - 同一 Job 最多一个 running：存在其它 running 步骤时本更新不生效（安全无操作）；
        - 成功时 attempt_count 原子 +1；
        - 只有更新成功才设置 current_step（保证 current_step 只指向 running 步骤）。
        """
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
                            ~_other_running_exists(job_id, step_name),
                        )
                        .values(
                            status="running",
                            started_at=_now(),
                            attempt_count=WorkflowStepORM.attempt_count + 1,
                        )
                    ),
                )
                # 只有步骤真正进入 running 才设置 current_step（避免旧步骤误设）
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
        """步骤成功：running → succeeded，写 completed_at（保留 attempt_count）。

        成功后若 current_step 仍指向该步骤则清空（current_step 只指向 running 步骤）。
        P06-06C：真实转换成功（rowcount>0）时对步骤终态计数并 observe 真实耗时
        （started_at 缺失则只计数不 observe；指标写入失败不影响业务）。
        """
        with self._sf() as session:
            try:
                row = session.execute(
                    select(WorkflowStepORM.started_at).where(
                        WorkflowStepORM.job_id == job_id,
                        WorkflowStepORM.step_name == step_name,
                        WorkflowStepORM.status.in_(_SUCCEEDABLE_SOURCES),
                    )
                ).scalar_one_or_none()
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(WorkflowStepORM)
                        .where(
                            WorkflowStepORM.job_id == job_id,
                            WorkflowStepORM.step_name == step_name,
                            WorkflowStepORM.status.in_(_SUCCEEDABLE_SOURCES),
                        )
                        .values(status="succeeded", completed_at=_now())
                    ),
                )
                if result.rowcount > 0:
                    self._clear_current_step_if_matches(session, job_id, step_name)
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise StepRecordError(
                    f"标记步骤成功失败 job={job_id} step={step_name}: {exc}"
                ) from exc
        if result.rowcount > 0:
            self._record_step_terminal_metric(step_name, "succeeded", row)

    def mark_step_failed(
        self,
        job_id: uuid.UUID,
        step_name: str,
        *,
        error_code: str,
        error_message: str,
        terminal: bool,
    ) -> None:
        """步骤失败：running → failed_retryable/failed_terminal，保存脱敏错误摘要。

        失败后若 current_step 仍指向该步骤则清空（current_step 只指向 running 步骤）。
        P06-06C：真实转换成功（rowcount>0）时对步骤终态计数并 observe 真实耗时。
        """
        sanitized = (error_message or "")[:_MAX_ERROR_MESSAGE]
        with self._sf() as session:
            try:
                row = session.execute(
                    select(WorkflowStepORM.started_at).where(
                        WorkflowStepORM.job_id == job_id,
                        WorkflowStepORM.step_name == step_name,
                        WorkflowStepORM.status.in_(_FAILABLE_SOURCES),
                    )
                ).scalar_one_or_none()
                result = cast(
                    CursorResult[Any],
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
                    ),
                )
                if result.rowcount > 0:
                    self._clear_current_step_if_matches(session, job_id, step_name)
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise StepRecordError(
                    f"标记步骤失败失败 job={job_id} step={step_name}: {exc}"
                ) from exc
        if result.rowcount > 0:
            status = "failed_terminal" if terminal else "failed_retryable"
            self._record_step_terminal_metric(step_name, status, row)

    @staticmethod
    def _record_step_terminal_metric(
        step_name: str,
        status: str,
        started_at_row: Any,
    ) -> None:
        """步骤终态指标：真实持续秒数 = now - started_at（缺失则只计数不 observe）。

        SQLite（测试）返回 offset-naive datetime：先补 UTC tzinfo 再做减法，
        避免 TypeError。指标写入失败由 metrics_events 内部脱敏处理，绝不改变业务结果。
        """
        from invest_research.infrastructure.observability.metrics_events import (
            count_step_terminal,
        )

        duration: float | None = None
        if started_at_row is not None:
            started = started_at_row
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            duration = max((_now() - started).total_seconds(), 0.0)
        count_step_terminal(step_name, status, duration)

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
                raise StepRecordError(f"收口 running 步骤失败 job={job_id}: {exc}") from exc

    def cancel_pending_steps(self, job_id: uuid.UUID) -> None:
        """Job 取消时收口步骤（P06-06B 收口）。

        - running → skipped（当前执行中的步骤）；
        - pending → skipped（后续未开始步骤）；
        - succeeded/failed_retryable/failed_terminal 保留（不删除历史）；
        - 清空 research_jobs.current_step；
        - 幂等：重复调用不改变任何状态。
        """
        with self._sf() as session:
            try:
                # running → skipped（只更新 running 来源）
                session.execute(
                    update(WorkflowStepORM)
                    .where(
                        WorkflowStepORM.job_id == job_id,
                        WorkflowStepORM.status == "running",
                    )
                    .values(status="skipped", completed_at=_now())
                )
                # pending → skipped（只更新 pending 来源；已成功/已失败不受影响）
                session.execute(
                    update(WorkflowStepORM)
                    .where(
                        WorkflowStepORM.job_id == job_id,
                        WorkflowStepORM.status == "pending",
                    )
                    .values(status="skipped")
                )
                # 清空 current_step（取消后无任何 running 步骤）
                session.execute(
                    update(ResearchJobORM)
                    .where(ResearchJobORM.id == job_id)
                    .values(current_step=None)
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise StepRecordError(f"取消收口步骤失败 job={job_id}: {exc}") from exc

    def cleanup_steps(self, job_id: uuid.UUID) -> None:
        """CancelStepCleanup 端口适配：委托给 ``cancel_pending_steps``。

        取消服务在任务真正取消成功后调用本方法收口步骤（不做任何自己的逻辑，
        保证二处收口语义完全一致）。
        """
        self.cancel_pending_steps(job_id)

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

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _clear_current_step_if_matches(session: Any, job_id: uuid.UUID, step_name: str) -> None:
        """仅当 current_step 仍指向该步骤时清空（条件更新，防误清其它 running 步骤）。

        场景：某步骤进入终态，但另一个代码路径可能已经把 current_step 指向了
        下一步；此时绝不能把下一步的 current_step 清掉。用 WHERE 条件精确限定。
        """
        session.execute(
            update(ResearchJobORM)
            .where(
                ResearchJobORM.id == job_id,
                ResearchJobORM.current_step == step_name,
            )
            .values(current_step=None)
        )
