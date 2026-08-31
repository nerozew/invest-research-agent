"""Idempotency-Key 幂等创建任务（P04-05）。

语义（验收：同 key 同请求复用，不同请求 409）：
- 客户端为创建任务提供 ``Idempotency-Key``（HTTP 头）。
- **同 key + 请求体一致**：复用首次结果，返回原 job_id（200/202）——不重复创建。
- **同 key + 请求体不一致**：抛 ``IdempotencyConflict``，由 API 返回 409。
- 幂等池只记录 "key → (job_id, status)"；请求体一致性由调用方传入 hash 参与判断
  （本任务用请求的规范性表示对比，保持最简）。

端口：
- ``IdempotencyStore``：按 key 读取/保存任务结果（含请求指纹）。
- ``CreateResearchJobService``（P04-02）负责真正创建任务。
"""

from __future__ import annotations

import uuid
from typing import Protocol

from pydantic import BaseModel

from invest_research.application.jobs import CreateResearchJobService
from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus


class StoredJob(BaseModel):
    """幂等池中保存的任务结果。"""

    job_id: uuid.UUID
    status: JobStatus
    request_fingerprint: str


class IdempotencyStore(Protocol):
    """按 Idempotency-Key 读取/保存任务结果。"""

    def get(self, key: str) -> StoredJob | None: ...
    def save(self, key: str, job: StoredJob) -> None: ...


class IdempotencyConflict(ValueError):
    """同一 Idempotency-Key 被不同的请求体使用（409）。"""


def _request_fingerprint(request: ResearchRequest) -> str:
    """请求体规范性指纹：同输入必同指纹，用于判断"是否同一请求"。"""
    return request.idempotency_fingerprint()


class CreateResearchJobIdempotentService:
    """幂等创建任务用例：同 key 同请求复用，异请求冲突。"""

    def __init__(
        self,
        *,
        job_service: CreateResearchJobService,
        idempotency_store: IdempotencyStore,
    ) -> None:
        self._job_service = job_service
        self._idempotency_store = idempotency_store

    def create(self, request: ResearchRequest, idempotency_key: str) -> tuple[StoredJob, bool]:
        """创建或复用；返回 (结果, 是否首次创建)。

        - 首次创建：返回 (job, True)，路由应返回 202。
        - 同 key 同请求复用：返回 (已有 job, False)，路由应返回 200。
        - 同 key 异请求：抛 IdempotencyConflict（409）。
        """
        fingerprint = _request_fingerprint(request)
        existing = self._idempotency_store.get(idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflict("Idempotency-Key 已被不同的请求体使用，禁止复用")
            return existing, False
        created = self._job_service.create(request)
        job = StoredJob(
            job_id=created.job_id,
            status=created.status,
            request_fingerprint=fingerprint,
        )
        self._idempotency_store.save(idempotency_key, job)
        return job, True
