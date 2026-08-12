"""Repository 层：JobRepository（P01-12）。

职责：把 ORM 映射与 SQLAlchemy 会话封装为“领域操作”，
上层（application/service）只调用 create/get/update_status，
不直接面对 SQLAlchemy。

事务边界：
- create / get 各自在自己的事务/会话中执行；
- update_status 是“条件更新”（乐观锁式）：仅当当前状态 == from_status
  才更新为 to_status，返回是否成功；失败不覆盖其他状态。

依赖方向：infrastructure -> domain（JobStatus 枚举来自 domain）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from invest_research.domain.status import JobStatus, StepStatus
from invest_research.infrastructure.db.models import (
    Artifact,
    ResearchJob,
    WorkflowStep,
)

SessionFactory = Callable[[], Session]


class JobRepository:
    """操作 research_jobs 表的仓储。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create(self, job: ResearchJob) -> ResearchJob:
        """插入并提交一个新任务；返回带 id/时间戳的持久化对象。"""
        with self._session_factory() as session:
            session.add(job)
            session.commit()
            session.refresh(job)
        return job

    def get(self, job_id: uuid.UUID) -> ResearchJob | None:
        """按 id 取回任务；不存在返回 None。"""
        with self._session_factory() as session:
            return cast(ResearchJob | None, session.get(ResearchJob, job_id))

    def update_status(
        self, job_id: uuid.UUID, from_status: JobStatus, to_status: JobStatus
    ) -> bool:
        """条件更新任务状态。

        仅当当前状态 == from_status 时更新为 to_status，返回 True；
        否则返回 False（不覆盖其他状态，也不抛错）。
        """
        with self._session_factory() as session:
            result = session.execute(
                update(ResearchJob)
                .where(ResearchJob.id == job_id, ResearchJob.status == from_status.value)
                .values(status=to_status.value)
            )
            affected = cast(CursorResult[Any], result).rowcount
            session.commit()
            return affected == 1


class StepRepository:
    """操作 workflow_steps 表的仓储（步骤级状态）。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create(self, step: WorkflowStep) -> WorkflowStep:
        """插入并提交一个步骤。"""
        with self._session_factory() as session:
            session.add(step)
            session.commit()
            session.refresh(step)
        return step

    def update_status(
        self, step_id: uuid.UUID, from_status: StepStatus, to_status: StepStatus
    ) -> bool:
        """条件更新步骤状态（乐观锁）。"""
        with self._session_factory() as session:
            result = session.execute(
                update(WorkflowStep)
                .where(WorkflowStep.id == step_id, WorkflowStep.status == from_status.value)
                .values(status=to_status.value)
            )
            affected = cast(CursorResult[Any], result).rowcount
            session.commit()
            return affected == 1

    def list_by_job(self, job_id: uuid.UUID) -> list[WorkflowStep]:
        """按 job 取回所有步骤（按 sequence_no 升序）。"""
        with self._session_factory() as session:
            return list(
                session.query(WorkflowStep)
                .filter(WorkflowStep.job_id == job_id)
                .order_by(WorkflowStep.sequence_no.asc())
            )


class ArtifactRepository:
    """操作 artifacts 表的仓储（重复写被唯一约束拒绝）。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create(self, artifact: Artifact) -> Artifact:
        """插入并提交一个工件；同 job_id+artifact_key 重复写会被数据库拒绝。"""
        with self._session_factory() as session:
            session.add(artifact)
            session.commit()
            session.refresh(artifact)
        return artifact
