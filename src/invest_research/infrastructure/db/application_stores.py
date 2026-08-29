"""SQLAlchemy 实现的 Application Store 适配器（P04-10A 生产 wiring）。

把 ORM 仓库/表组装成 application layer 需要的 7 个端口：
- ``SqlJobStore``：JobStore —— 创建 ResearchJob（含 pending 初始状态）
- ``SqlJobQueryStore``：JobQueryStore —— 读取 JobSnapshot（含 steps，按 sequence_no 排序）
- ``SqlJobListStore``：JobListStore —— keyset 游标稳定分页列出任务
- ``SqlIdempotencyStore``：IdempotencyStore —— 独立 idempotency_keys 表保存
  key → (job_id, status, request_fingerprint)，DB UNIQUE 兜底防并发重复
- ``SqlCancelStatusWriter``：CancelStatusWriter —— 条件 UPDATE（乐观锁）pending/running→cancelled
- ``SqlArtifactCatalogStore``：ArtifactCatalogStore —— 读 artifacts 表清单
- ``SqlArtifactContentStore``：ArtifactContentStore —— 仅读已登记工件内容（防路径穿越：
  不自信 storage_uri，而是用 job_id + artifact_key 规范相对路径；先查登记再读文件）

事务边界：每个方法一个 session，独立提交/回滚；失败抛分类异常。
依赖方向：infrastructure -> application/domain（端口/枚举），不反向。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from invest_research.application.artifacts import ArtifactInfo
from invest_research.application.idempotency import StoredJob
from invest_research.application.job_listing import JobListCursor, JobListEntry
from invest_research.application.jobs import (
    JobSnapshot,
    PerformanceSnapshot,
    StepSnapshot,
    compute_duration_seconds,
)
from invest_research.application.outbox import (
    EVENT_TYPE_JOB_CREATED,
    OutboxEventSnapshot,
)
from invest_research.domain.annual_pipeline import ResearchMode
from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus, StepStatus
from invest_research.infrastructure.db.models import (
    Artifact as ArtifactORM,
)
from invest_research.infrastructure.db.models import (
    IdempotencyKeyRow as IdempotencyKeyORM,
)
from invest_research.infrastructure.db.models import (
    OutboxEvent as OutboxEventORM,
)
from invest_research.infrastructure.db.models import (
    ResearchJob as ResearchJobORM,
)
from invest_research.infrastructure.db.models import (
    WorkflowStep as WorkflowStepORM,
)
from invest_research.infrastructure.db.repositories import SessionFactory
from invest_research.tools.artifact_store import ArtifactStore

# WS3：从 07_manifest.json 读取 per-job 性能/成本；文件缺失/损坏返回 None（不伪造）。
PerformanceLoader = Callable[[uuid.UUID], PerformanceSnapshot | None]


def _int_or_none(value: object) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return None


def _manifest_performance_loader(
    artifact_root: str | Path,
    pricing: dict[str, Any] | None,
) -> PerformanceLoader:
    """构造从 07_manifest.json 读性能/成本的 loader（供 SqlJobQueryStore 注入）。"""

    def load(job_id: uuid.UUID) -> PerformanceSnapshot | None:
        from invest_research.application.costing import (
            estimate_job_cost,
            pricing_entry_for,
        )

        try:
            content = ArtifactStore(Path(artifact_root) / str(job_id)).read("07_manifest.json")
        except (KeyError, OSError):
            return None
        try:
            manifest = json.loads(content)
        except ValueError:
            return None
        if not isinstance(manifest, dict):
            return None
        perf = manifest.get("performance") or {}
        usage = perf.get("token_usage") or {}
        inv = (manifest.get("evidence") or {}).get("invocation_summary") or {}
        if not isinstance(perf, dict) or not isinstance(usage, dict) or not isinstance(inv, dict):
            return None
        cost = estimate_job_cost(
            input_tokens=_int_or_none(usage.get("prompt_tokens")),
            output_tokens=_int_or_none(usage.get("completion_tokens")),
            pricing_entry=pricing_entry_for(
                pricing, manifest.get("research_profile") or manifest.get("research_mode")
            ),
        )
        return PerformanceSnapshot(
            llm_calls=_int_or_none(perf.get("llm_calls")),
            input_tokens=_int_or_none(usage.get("prompt_tokens")),
            output_tokens=_int_or_none(usage.get("completion_tokens")),
            cached_input_tokens=_int_or_none(usage.get("cached_prompt_tokens")),
            total_tokens=_int_or_none(usage.get("total_tokens")),
            tool_calls_total=sum(int(v) for v in inv.values() if isinstance(v, (int, float))),
            estimated_cost_usd=cost,
            pricing_as_of=str((pricing or {}).get("as_of")) if pricing else None,
        )

    return load


_LOGGER = logging.getLogger(__name__)

__all__ = [
    "SqlJobStore",
    "SqlJobQueryStore",
    "SqlJobListStore",
    "SqlIdempotencyStore",
    "SqlCancelStatusWriter",
    "SqlArtifactCatalogStore",
    "SqlArtifactContentStore",
    "SqlOutboxStore",
]


class _StoreError(RuntimeError):
    """Store 层通用错误（供 wiring/API 转 503 等）。"""


class SqlJobStore:
    """JobStore：把待创建任务持久化为 ResearchJob 行。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    def create(self, *, request: ResearchRequest, job_id: uuid.UUID) -> None:
        from invest_research.infrastructure.observability.tracing import (
            current_trace_carrier,
        )

        with self._sf() as session:
            row = ResearchJobORM(
                id=job_id,
                input_company=request.input_company,
                as_of_date=request.as_of_date,
                language=request.language,
                requested_forms=list(request.requested_forms),
                # P06-06A：每任务研究档位随任务持久化
                research_profile=request.research_profile,
                research_mode=request.research_mode.value,
                status=JobStatus.PENDING.value,
                config_snapshot={},
            )
            try:
                session.add(row)
                # P05.5-deploy-fix：先 flush research_jobs 再插 outbox——
                # SQLAlchemy UOW 实测按 outbox→job 顺序刷出（不保证 FK 依赖顺序），
                # PostgreSQL 会因 outbox_events_job_id_fkey 直接报 FK 违反。
                session.flush()
                session.add(
                    OutboxEventORM(
                        job_id=job_id,
                        event_type=EVENT_TYPE_JOB_CREATED,
                        payload={
                            "job_id": str(job_id),
                            "trace_context": current_trace_carrier(),
                        },
                        status="pending",
                        attempts=0,
                    )
                )
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                # 记录真实约束错误（NOT NULL/类型/唯一）便于部署排障。
                _LOGGER.error("创建 job 失败: %s", exc.orig)
                raise _StoreError(f"创建 job 失败: {exc.orig}") from exc
        # P06-06C：Job 真实创建成功（事务提交后）才计数 pending。
        from invest_research.infrastructure.observability.metrics_events import (
            count_research_job,
        )

        count_research_job("pending")


def _coerce_step_status(raw: str | None) -> StepStatus:
    """从数据库字符串构造 ``StepStatus``。

    ``execution_recorder`` 历史版本把失败步骤写成字面 ``"failed"``，而
    ``StepStatus`` 只有 ``failed_retryable``/``failed_terminal``，直接取值会
    ``ValueError`` 导致 API 500。此处把遗留 ``"failed"`` 映射为终态失败。
    """
    if raw is None:
        return StepStatus.FAILED_TERMINAL
    if raw == "failed":
        return StepStatus.FAILED_TERMINAL
    return StepStatus(raw)


class SqlJobQueryStore:
    """JobQueryStore：读取 Job + steps 转 JobSnapshot。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        performance_loader: PerformanceLoader | None = None,
    ) -> None:
        self._sf = session_factory
        self._performance_loader = performance_loader

    def get(self, job_id: uuid.UUID) -> JobSnapshot | None:
        with self._sf() as session:
            job = session.get(ResearchJobORM, job_id)
            if job is None:
                return None
            steps = (
                session.execute(
                    select(WorkflowStepORM)
                    .where(WorkflowStepORM.job_id == job_id)
                    .order_by(WorkflowStepORM.sequence_no.asc())
                )
                .scalars()
                .all()
            )
        step_snapshots = tuple(
            StepSnapshot(
                step_name=s.step_name,
                sequence_no=s.sequence_no,
                status=_coerce_step_status(s.status),
                attempt_count=s.attempt_count,
                error_code=s.error_json.get("error_code") if s.error_json else None,
                error_message=s.error_json.get("error_message") if s.error_json else None,
                started_at=s.started_at,
                completed_at=s.completed_at,
            )
            for s in steps
        )
        annual_nodes = None
        if job.research_mode == ResearchMode.ANNUAL_DEEP.value:
            from invest_research.infrastructure.db.annual_node_store import SqlAnnualNodeStore

            annual_nodes = SqlAnnualNodeStore(self._sf).snapshot(job_id=job_id)
        return JobSnapshot.build(
            job_id=job.id,
            status=JobStatus(job.status),
            current_step=job.current_step,
            research_profile=job.research_profile,
            research_mode=ResearchMode(job.research_mode),
            annual_nodes=annual_nodes,
            error_code=job.error_code,
            error_message=job.error_message,
            failure_stage=job.failure_stage,
            started_at=job.started_at,
            completed_at=job.completed_at,
            steps=step_snapshots,
            performance=(
                self._performance_loader(job_id) if self._performance_loader is not None else None
            ),
        )


class SqlJobListStore:
    """JobListStore：按 keyset 游标稳定分页列出任务。

    - 排序：``created_at DESC, job_id DESC``（created_at 相同时用 job_id 打破平局），
      保证分页无重复、无遗漏；
    - 过滤：可选 status（JobStatus 枚举）；
    - 分页：``before=(created_at, job_id)`` 返回严格排在它之前的行，最多 ``limit`` 条；
    - 安全：只投影列表 DTO 字段，不读取 config_snapshot/idempotency_key。
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        limit: int,
        before: JobListCursor | None,
    ) -> tuple[JobListEntry, ...]:
        with self._sf() as session:
            stmt = select(ResearchJobORM)
            if status is not None:
                stmt = stmt.where(ResearchJobORM.status == status.value)
            if before is not None:
                created_at, job_id = before
                stmt = stmt.where(_before_keyset(created_at, job_id))
            stmt = stmt.order_by(
                ResearchJobORM.created_at.desc(),
                ResearchJobORM.id.desc(),
            ).limit(limit)
            rows = session.execute(stmt).scalars().all()
        return tuple(
            JobListEntry(
                job_id=r.id,
                input_company=r.input_company,
                as_of_date=r.as_of_date,
                language=r.language,
                status=JobStatus(r.status),
                current_step=r.current_step,
                research_profile=r.research_profile,
                research_mode=ResearchMode(r.research_mode),
                error_code=r.error_code,
                created_at=r.created_at,
                started_at=r.started_at,
                completed_at=r.completed_at,
                duration_seconds=compute_duration_seconds(r.started_at, r.completed_at),
            )
            for r in rows
        )


def _before_keyset(created_at: datetime, job_id: uuid.UUID) -> ColumnElement[bool]:
    """keyset 条件：稳定序 ``(created_at, job_id)`` 小于给定位置才返回。"""
    return or_(
        ResearchJobORM.created_at < created_at,
        (ResearchJobORM.created_at == created_at) & (ResearchJobORM.id < job_id),
    )


class SqlIdempotencyStore:
    """IdempotencyStore：独立 idempotency_keys 表（key 唯一兜底防并发）。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    def get(self, key: str) -> StoredJob | None:
        with self._sf() as session:
            row = session.execute(
                select(IdempotencyKeyORM).where(IdempotencyKeyORM.key == key)
            ).scalar_one_or_none()
        if row is None:
            return None
        return StoredJob(
            job_id=row.job_id,
            status=JobStatus(row.status),
            request_fingerprint=row.request_fingerprint,
        )

    def save(self, key: str, job: StoredJob) -> None:
        with self._sf() as session:
            row = IdempotencyKeyORM(
                key=key,
                job_id=job.job_id,
                status=job.status.value,
                request_fingerprint=job.request_fingerprint,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                # 并发下同 key 已存在：读取既有记录，交由上层判断是否幂等复用
                session.rollback()
                with self._sf() as s2:
                    existing = s2.execute(
                        select(IdempotencyKeyORM).where(IdempotencyKeyORM.key == key)
                    ).scalar_one_or_none()
                if existing is None:
                    raise _StoreError(f"幂等键保存冲突且读取失败: {key}")
                # 不在这里抛：让上层用 get() 重新判断 fingerprint；
                # 但 save 被调用方视为"首次创建写入"，冲突意味着并发竞争，
                # 统一按冲突处理，由调用方决定。
                raise _StoreError(f"幂等键并发冲突: {key}")


class SqlCancelStatusWriter:
    """CancelStatusWriter：pending/running → cancelled 的条件更新（乐观锁）。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    def cancel_from_pending(self, job_id: uuid.UUID) -> bool:
        return self._try_cancel(job_id, JobStatus.PENDING)

    def cancel_from_running(self, job_id: uuid.UUID) -> bool:
        return self._try_cancel(job_id, JobStatus.RUNNING)

    def _try_cancel(self, job_id: uuid.UUID, from_status: JobStatus) -> bool:
        with self._sf() as session:
            result = session.execute(
                update(ResearchJobORM)
                .where(
                    ResearchJobORM.id == job_id,
                    ResearchJobORM.status == from_status.value,
                )
                .values(status=JobStatus.CANCELLED.value)
            )
            # CursorResult 类型未声明 rowcount；运行时行为正确，
            # 用 inline ignore 保持 mypy strict 通过。
            affected = int(result.rowcount)  # type: ignore[attr-defined]
            session.commit()
        # P06-06C：仅真实转换成功（乐观锁 rowcount=1）时计数 cancelled，
        # 幂等重复取消不重复计数。
        if affected == 1:
            from invest_research.infrastructure.observability.metrics_events import (
                count_research_job,
            )

            count_research_job("cancelled")
        return affected == 1


class SqlArtifactCatalogStore:
    """ArtifactCatalogStore：列出某 job 已登记的工件。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    def list_artifacts(self, job_id: uuid.UUID) -> tuple[ArtifactInfo, ...]:
        with self._sf() as session:
            rows = (
                session.execute(
                    select(ArtifactORM)
                    .where(ArtifactORM.job_id == job_id)
                    .order_by(ArtifactORM.created_at.asc())
                )
                .scalars()
                .all()
            )
        return tuple(
            ArtifactInfo(
                artifact_key=r.artifact_key,
                artifact_type=r.artifact_type,
                schema_version=r.schema_version,
                storage_uri=r.storage_uri,
                content_checksum=r.content_checksum,
                byte_size=r.byte_size,
            )
            for r in rows
        )


class SqlArtifactContentStore:
    """ArtifactContentStore：仅允许读取已登记工件的安全内容。

    安全模型：
    - 先查 artifacts 表确认 (job_id, artifact_key) 已登记（不属于该 job → None）；
    - 不信任 storage_uri 拼路径；改用 ``artifact_root / <job_id> / <artifact_key>``
      规范相对路径；artifact_key 在 application 层已做非法字符拦截，
      此处再加一层 Path 前缀校验（防目录穿越）。
    """

    def __init__(self, session_factory: SessionFactory, artifact_root: str) -> None:
        self._sf = session_factory
        self._root = Path(artifact_root).resolve()

    def read(self, job_id: uuid.UUID, artifact_key: str) -> bytes | None:
        with self._sf() as session:
            row = session.execute(
                select(ArtifactORM).where(
                    ArtifactORM.job_id == job_id,
                    ArtifactORM.artifact_key == artifact_key,
                )
            ).scalar_one_or_none()
        if row is None:
            return None

        # 构造规范相对路径并防御穿越
        rel = PurePosixPath(str(job_id)) / artifact_key
        candidate = (self._root / Path(*rel.parts)).resolve()
        if not candidate.is_relative_to(self._root):
            return None
        if not candidate.is_file():
            return None
        return candidate.read_bytes()


class SqlOutboxStore:
    """OutboxStore：SQLAlchemy 实现 outbox_events 表读写（P05-03B）。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    def find_undelivered(self, *, limit: int) -> list[OutboxEventSnapshot]:
        with self._sf() as session:
            rows = (
                session.execute(
                    select(OutboxEventORM)
                    .where(OutboxEventORM.status == "pending")
                    .order_by(OutboxEventORM.created_at.asc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [self._snapshot(r) for r in rows]

    def find_by_job(self, job_id: uuid.UUID, event_type: str) -> OutboxEventSnapshot | None:
        with self._sf() as session:
            row = session.execute(
                select(OutboxEventORM).where(
                    OutboxEventORM.job_id == job_id,
                    OutboxEventORM.event_type == event_type,
                )
            ).scalar_one_or_none()
        return self._snapshot(row) if row is not None else None

    @staticmethod
    def _snapshot(row: OutboxEventORM) -> OutboxEventSnapshot:
        raw_context = row.payload.get("trace_context", {})
        trace_context = (
            {
                str(key): str(value)
                for key, value in raw_context.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            if isinstance(raw_context, dict)
            else {}
        )
        return OutboxEventSnapshot(
            event_id=row.id,
            job_id=row.job_id,
            event_type=row.event_type,
            attempts=row.attempts,
            trace_context=trace_context,
        )

    def mark_claimed(self, event_id: uuid.UUID) -> bool:
        with self._sf() as session:
            result = session.execute(
                update(OutboxEventORM)
                .where(
                    OutboxEventORM.id == event_id,
                    OutboxEventORM.status == "pending",
                )
                .values(status="claimed")
            )
            affected = int(result.rowcount)  # type: ignore[attr-defined]
            session.commit()
            return affected == 1

    def mark_sent(self, event_id: uuid.UUID) -> None:
        with self._sf() as session:
            session.execute(
                update(OutboxEventORM).where(OutboxEventORM.id == event_id).values(status="sent")
            )
            session.commit()

    def requeue(self, event_id: uuid.UUID) -> None:
        with self._sf() as session:
            session.execute(
                update(OutboxEventORM)
                .where(OutboxEventORM.id == event_id)
                .values(status="pending", attempts=OutboxEventORM.attempts + 1)
            )
            session.commit()

    def mark_failed(self, event_id: uuid.UUID) -> None:
        with self._sf() as session:
            session.execute(
                update(OutboxEventORM).where(OutboxEventORM.id == event_id).values(status="failed")
            )
            session.commit()
