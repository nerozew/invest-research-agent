"""前端 typed DTO 模型（P04-UI-01）。

前端 client 的后端响应模型，与 FastAPI 返回的 JSON 结构一一对应
（见 `docs/02-ARCHITECTURE.md §11`：前端是 FastAPI 的 HTTP 客户端）。

设计原则：
- 不复用 ``api/jobs.py`` / ``api/health.py`` 的 DTO（那些属于后端，前端是独立
  进程，只依赖 HTTP JSON 契约）；也不直接 import application/infrastructure。
- 复用 ``domain`` 的纯枚举（``JobStatus`` / ``StepStatus``）与 ``ResearchRequest``
  请求体模型：它们只依赖 pydantic，无后端耦合。
- frozen=True 让 DTO 创建后不可变，防止 UI 层意外篡改。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus, StepStatus

__all__ = [
    "ArtifactInfo",
    "CancelJobResponse",
    "CreateResearchJobRequest",
    "CreateResearchJobResponse",
    "DependencyStatus",
    "HealthResponse",
    "JobListEntry",
    "JobListPage",
    "JobSnapshot",
    "ReadinessResponse",
    "StepSnapshot",
]

# 请求体重用领域模型（字段即 FR-001 输入：公司/ticker、as_of_date、语言、表单）。
CreateResearchJobRequest = ResearchRequest


class CreateResearchJobResponse(BaseModel):
    """POST /v1/research-jobs 的响应体（HTTP 200/202）。"""

    model_config = ConfigDict(frozen=True)

    job_id: uuid.UUID
    status: JobStatus


class HealthResponse(BaseModel):
    """GET /health 的响应体（Liveness 探针）。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"]
    service: str


class DependencyStatus(BaseModel):
    """GET /readiness 中单个依赖的就绪状态。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "unavailable"]
    error_code: str | None = None


class ReadinessResponse(BaseModel):
    """GET /readiness 的响应体（Readiness 探针）。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["ready", "not_ready"]
    ready: bool
    database: DependencyStatus
    redis: DependencyStatus


class StepSnapshot(BaseModel):
    """GET /v1/research-jobs/{id} 中单个工作流步骤的快照。"""

    model_config = ConfigDict(frozen=True)

    step_name: str
    sequence_no: int
    status: StepStatus
    attempt_count: int
    error_code: str | None = None
    error_message: str | None = None
    duration_seconds: float | None = None


class JobSnapshot(BaseModel):
    """GET /v1/research-jobs/{id} 的响应体（任务状态快照）。"""

    model_config = ConfigDict(frozen=True)

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
    steps: tuple[StepSnapshot, ...] = Field(default_factory=tuple)


class JobListEntry(BaseModel):
    """GET /v1/research-jobs 的列表条目。"""

    model_config = ConfigDict(frozen=True)

    job_id: uuid.UUID
    input_company: str
    as_of_date: date
    language: str
    status: JobStatus
    current_step: str | None = None
    # P06-06A：每任务研究档位（fast/deep），默认 deep（旧任务兼容）
    research_profile: str = "deep"
    error_code: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class JobListPage(BaseModel):
    """GET /v1/research-jobs 的分页响应。"""

    model_config = ConfigDict(frozen=True)

    items: tuple[JobListEntry, ...] = Field(default_factory=tuple)
    next_cursor: str | None = None


class CancelJobResponse(BaseModel):
    """DELETE /v1/research-jobs/{id} 的响应体。"""

    model_config = ConfigDict(frozen=True)

    job_id: uuid.UUID
    status: JobStatus
    did_cancel: bool = False
    already_cancelled: bool | None = None


class ArtifactInfo(BaseModel):
    """GET /v1/research-jobs/{id}/artifacts 的清单条目。"""

    model_config = ConfigDict(frozen=True)

    artifact_key: str
    artifact_type: str
    schema_version: str | None = None
    storage_uri: str
    content_checksum: str
    byte_size: int = Field(ge=0)
