"""任务用例与端口（P04-02 创建 + P04-03 查询）。

端口（Protocol）：``JobStore``（创建）、``JobQueryStore``（查询）定义持久化能力，
由 infrastructure 提供真实实现；本层不导入 SQLAlchemy/Redis/FastAPI，保持依赖方向
application -> domain。

- ``CreateResearchJobService``：创建用例（FR-001）。校验由 domain.ResearchRequest 完成
  （坏请求在 API 层直接 422），本服务生成 job_id 并委托存储写入，返回 202 语义的 job_id。
- ``GetResearchJobService``：查询用例（FR-013）。返回 JobSnapshot（状态/步骤/错误/耗时）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus, StepStatus


class CreatedJob(BaseModel):
    """创建成功的结果（API 层据此返回 202 + job_id）。"""

    job_id: uuid.UUID
    status: JobStatus


class JobStore(Protocol):
    """任务持久化端口。实现者负责写库并保证成功后可见。"""

    def create(self, *, request: ResearchRequest, job_id: uuid.UUID) -> None: ...


class CreateResearchJobService:
    """创建研究任务用例（FR-001）。"""

    def __init__(self, store: JobStore) -> None:
        self._store = store

    def create(self, request: ResearchRequest) -> CreatedJob:
        job_id = uuid.uuid4()
        self._store.create(request=request, job_id=job_id)
        return CreatedJob(job_id=job_id, status=JobStatus.PENDING)


# ---------------------------------------------------------------------------
# 查询任务状态（P04-03）：FR-013 状态查询
# ---------------------------------------------------------------------------


def compute_duration_seconds(
    started_at: datetime | None, completed_at: datetime | None
) -> float | None:
    """计算耗时秒数；未开始（started_at 缺失）时返回 None。"""
    if started_at is None:
        return None
    end = completed_at if completed_at is not None else started_at
    return round((end - started_at).total_seconds(), 3)


class StepSnapshot(BaseModel):
    """单个工作流步骤的状态快照（供查询接口返回）。"""

    step_name: str
    sequence_no: int
    status: StepStatus
    attempt_count: int
    error_code: str | None = None
    error_message: str | None = None
    duration_seconds: float | None = None

    @classmethod
    def build(
        cls,
        *,
        step_name: str,
        sequence_no: int,
        status: StepStatus,
        attempt_count: int,
        error_code: str | None,
        error_message: str | None,
        started_at: datetime | None,
        completed_at: datetime | None,
    ) -> "StepSnapshot":
        return cls(
            step_name=step_name,
            sequence_no=sequence_no,
            status=status,
            attempt_count=attempt_count,
            error_code=error_code,
            error_message=error_message,
            duration_seconds=compute_duration_seconds(started_at, completed_at),
        )


class JobSnapshot(BaseModel):
    """任务状态快照（FR-013：状态、当前步骤、步骤耗时、重试次数、错误码、耗时）。"""

    job_id: uuid.UUID
    status: JobStatus
    current_step: str | None = None
    # P06-06A：每任务研究档位（fast/deep），默认 deep（旧任务兼容）
    research_profile: str = "deep"
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    steps: tuple[StepSnapshot, ...] = ()

    @classmethod
    def build(
        cls,
        *,
        job_id: uuid.UUID,
        status: JobStatus,
        current_step: str | None = None,
        research_profile: str = "deep",
        error_code: str | None = None,
        error_message: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        steps: tuple[StepSnapshot, ...] = (),
    ) -> "JobSnapshot":
        return cls(
            job_id=job_id,
            status=status,
            current_step=current_step,
            research_profile=research_profile,
            error_code=error_code,
            error_message=error_message,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=compute_duration_seconds(started_at, completed_at),
            steps=steps,
        )


class JobQueryStore(Protocol):
    """任务查询端口。实现者按 job_id 读取任务及其步骤，不存在返回 None。"""

    def get(self, job_id: uuid.UUID) -> JobSnapshot | None: ...


class GetResearchJobService:
    """查询研究任务状态用例（FR-013）。"""

    def __init__(self, store: JobQueryStore) -> None:
        self._store = store

    def get(self, job_id: uuid.UUID) -> JobSnapshot | None:
        return self._store.get(job_id)
