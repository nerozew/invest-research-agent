"""任务列表用例与端口（P04-UI-06）。

- ``JobListEntry``：列表条目 DTO（不含 config_snapshot、数据库 URL、密钥或内部路径）。
- ``JobListPage``：分页响应（items + next_cursor）。
- ``JobListStore``：持久化端口，只负责按 keyset 位置返回「最多 limit 条」的倒序列表。
- ``InvalidJobListCursor``：非法 cursor 时由 Service 抛出（API 转 422）。
- ``ListResearchJobService``：用例。负责：
  1. cursor 字符串 ⇄ keyset 位置（created_at, job_id）的 URL-safe 编解码；
  2. 请求 limit+1 条判断是否还有下一页，裁剪后生成 next_cursor；
  3. 稳定排序：``created_at DESC, job_id DESC``（created_at 相同时用 job_id 打破平局）。

依赖方向：本模块只依赖 domain（状态枚举）与 pydantic，不导入 SQLAlchemy/FastAPI。
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel

from invest_research.domain.annual_pipeline import ResearchMode
from invest_research.domain.status import JobStatus

__all__ = [
    "InvalidJobListCursor",
    "JobListEntry",
    "JobListPage",
    "JobListStore",
    "ListResearchJobService",
    "decode_cursor",
    "encode_cursor",
]

# keyset 位置：稳定排序键 (created_at, job_id)。created_at 倒序为主键，
# 相同时以 job_id 倒序打破平局，保证分页无重复、无遗漏。
JobListCursor = tuple[datetime, uuid.UUID]


class JobListEntry(BaseModel):
    """单个任务的列表条目（只暴露列表所需字段，不暴露 config_snapshot）。"""

    job_id: uuid.UUID
    input_company: str
    as_of_date: date
    language: str
    status: JobStatus
    current_step: str | None = None
    # P06-06A：每任务研究档位（fast/deep），默认 deep（旧任务兼容）
    research_profile: str = "deep"
    # P07-12：列表同样必须区分年度 DAG 与 legacy 串行任务；旧响应保持 legacy。
    research_mode: ResearchMode = ResearchMode.LEGACY
    error_code: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None


class JobListPage(BaseModel):
    """GET /v1/research-jobs 的响应体。"""

    items: tuple[JobListEntry, ...] = ()
    next_cursor: str | None = None


class JobListStore(Protocol):
    """任务列表持久化端口。

    ``before`` 为 keyset 位置：只返回排在它之前（更早）的任务。
    实现者负责稳定排序（created_at DESC, job_id DESC）与 status 过滤，
    并返回最多 ``limit`` 条。
    """

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        limit: int,
        before: JobListCursor | None,
    ) -> tuple[JobListEntry, ...]: ...


class InvalidJobListCursor(ValueError):
    """cursor 无法解析或无符号字段缺失（API 转 422）。"""


def encode_cursor(position: JobListCursor) -> str:
    """把 keyset 位置编码为 URL-safe base64（不带 padding）。"""
    created_at, job_id = position
    payload = {"t": created_at.isoformat(), "id": str(job_id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> JobListCursor:
    """把 URL-safe base64 解码为 keyset 位置；非法输入抛 InvalidJobListCursor。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = datetime.fromisoformat(payload["t"])
        job_id = uuid.UUID(payload["id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise InvalidJobListCursor("cursor 非法") from exc
    return created_at, job_id


class ListResearchJobService:
    """任务列表用例（P04-UI-06）。"""

    def __init__(self, store: JobListStore) -> None:
        self._store = store

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        limit: int,
        cursor: str | None,
    ) -> JobListPage:
        before = decode_cursor(cursor) if cursor is not None else None
        # 多取一条用于判断是否还有下一页；裁剪后生成 next_cursor。
        raw = self._store.list_jobs(status=status, limit=limit + 1, before=before)
        has_more = len(raw) > limit
        items = tuple(raw[:limit])
        next_cursor: str | None = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor((last.created_at, last.job_id))
        return JobListPage(items=items, next_cursor=next_cursor)
