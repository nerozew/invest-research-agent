"""P07-10 年度 DAG 的 SQLAlchemy 事实来源与诊断读模型。

该存储不派发任务、不调用工具；每次写操作只在本地事务内更新节点并追加事件。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import func, select

from invest_research.domain.annual_node_runtime import (
    AnnualNodeEventSnapshot,
    AnnualNodeEventType,
    AnnualNodeGraphSnapshot,
    AnnualNodeTransitionError,
    AnnualNodeWait,
    transition_annual_node,
)
from invest_research.domain.annual_pipeline import (
    NodeDependency,
    ResearchNode,
    ResearchNodeStatus,
)
from invest_research.infrastructure.db.models import (
    AnnualNodeDependency,
    AnnualNodeEvent,
    AnnualResearchNode,
    ResearchJob,
)
from invest_research.infrastructure.db.repositories import SessionFactory

__all__ = ["AnnualNodeGraphError", "SqlAnnualNodeStore"]


def _utc_now() -> datetime:
    """返回 timezone-aware UTC 当前时间，供年度节点持久化统一使用。"""
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """标准化数据库时间。

    PostgreSQL ``timestamptz`` 通常返回 aware 值，但 SQLite 和旧数据路径可能返回
    naive 值。年度节点所有时间均按 UTC 写入，故 naive 值按 UTC 解释，避免混合
    时区相减导致诊断或运行时失败。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

_EVENT_PAYLOAD_KEYS = frozenset(
    {"reason_code", "artifact_keys", "attempt_count", "elapsed_seconds", "remaining_budget"}
)
_BUDGET_REASON_CODES = frozenset(
    {
        "decision_budget_exhausted",
        "time_budget_exhausted",
        "no_evidence_gain",
        "tool_budget_exhausted",
        "confirmed_unavailable",
        "supplement_already_attempted",
    }
)


class AnnualNodeGraphError(ValueError):
    """年度节点图违反 job 边界、唯一性或 DAG 约束。"""


class SqlAnnualNodeStore:
    """独立年度节点存储；不读取或写入 ``workflow_steps`` / Outbox。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    def create_graph(
        self,
        *,
        job_id: uuid.UUID,
        nodes: tuple[ResearchNode, ...],
        dependencies: tuple[NodeDependency, ...] = (),
    ) -> AnnualNodeGraphSnapshot:
        """原子创建图；同一份完全相同的图重复提交时直接复用。"""
        self._validate_graph_input(nodes, dependencies)
        with self._sf() as session:
            try:
                if session.get(ResearchJob, job_id) is None:
                    raise AnnualNodeGraphError("年度节点图所属 job 不存在")
                existing = list(
                    session.execute(
                        select(AnnualResearchNode).where(AnnualResearchNode.job_id == job_id)
                    )
                    .scalars()
                    .all()
                )
                if existing:
                    self._assert_same_graph(existing, session, job_id, nodes, dependencies)
                    return self._snapshot(session, job_id)

                now = _utc_now()
                rows: dict[str, AnnualResearchNode] = {}
                for node in nodes:
                    row = AnnualResearchNode(
                        job_id=job_id,
                        node_key=node.node_key,
                        node_kind=node.kind.value,
                        status=node.status.value,
                        attempt_count=node.attempt_count,
                        max_attempts=node.max_attempts,
                        input_fingerprint=node.input_fingerprint,
                        output_artifact_keys=list(node.output_artifact_keys),
                        blocked_reason=node.blocked_reason,
                        error_code=node.error_code,
                    )
                    session.add(row)
                    rows[node.node_key] = row
                session.flush()
                for row in rows.values():
                    self._append_event(
                        session,
                        row=row,
                        event_type=AnnualNodeEventType.NODE_CREATED,
                        previous_status=None,
                        new_status=ResearchNodeStatus(row.status),
                        payload={"attempt_count": row.attempt_count},
                        now=now,
                    )
                for edge in dependencies:
                    session.add(
                        AnnualNodeDependency(
                            job_id=job_id,
                            upstream_node_id=rows[edge.upstream_node_key].id,
                            downstream_node_id=rows[edge.downstream_node_key].id,
                        )
                    )
                session.flush()
                snapshot = self._snapshot(session, job_id)
                session.commit()
                return snapshot
            except Exception:
                session.rollback()
                raise

    def add_dependency(
        self, *, job_id: uuid.UUID, upstream_node_key: str, downstream_node_key: str
    ) -> AnnualNodeGraphSnapshot:
        """给既有图增加一条边，并检查 job 边界、重复、自环与环。"""
        edge = NodeDependency(
            upstream_node_key=upstream_node_key, downstream_node_key=downstream_node_key
        )
        with self._sf() as session:
            try:
                rows = self._rows_by_key(session, job_id)
                if edge.upstream_node_key not in rows or edge.downstream_node_key not in rows:
                    raise AnnualNodeGraphError("依赖两端必须同属指定 job")
                current_edges = self._edges_by_key(session, job_id, rows)
                if edge in current_edges:
                    raise AnnualNodeGraphError("年度节点依赖边重复")
                self._assert_acyclic(tuple(rows), tuple((*current_edges, edge)))
                session.add(
                    AnnualNodeDependency(
                        job_id=job_id,
                        upstream_node_id=rows[edge.upstream_node_key].id,
                        downstream_node_id=rows[edge.downstream_node_key].id,
                    )
                )
                session.flush()
                snapshot = self._snapshot(session, job_id)
                session.commit()
                return snapshot
            except Exception:
                session.rollback()
                raise

    def transition(
        self,
        *,
        job_id: uuid.UUID,
        node_key: str,
        target: ResearchNodeStatus,
        error_code: str | None = None,
        blocked_reason: str | None = None,
        artifact_keys: tuple[str, ...] = (),
    ) -> ResearchNode:
        """原子转换状态并追加审计事件；失败终态会阻塞所有下游。"""
        with self._sf() as session:
            try:
                row = self._require_row(session, job_id, node_key)
                current = ResearchNodeStatus(row.status)
                if current is target:
                    return self._to_node(row)
                transition_annual_node(current, target)
                now = _utc_now()
                if target is ResearchNodeStatus.RUNNING:
                    if row.attempt_count >= row.max_attempts:
                        raise AnnualNodeTransitionError("节点尝试次数已耗尽")
                    row.attempt_count += 1
                    row.started_at = now
                if target is ResearchNodeStatus.BLOCKED:
                    if not blocked_reason:
                        raise AnnualNodeTransitionError("blocked 节点必须提供 blocked_reason")
                    row.blocked_reason = blocked_reason
                if target is not ResearchNodeStatus.BLOCKED:
                    row.blocked_reason = None
                if target.is_terminal:
                    row.completed_at = now
                row.status = target.value
                row.error_code = error_code
                if artifact_keys:
                    row.output_artifact_keys = list(artifact_keys)
                self._append_event(
                    session,
                    row=row,
                    event_type=AnnualNodeEventType.STATE_CHANGED,
                    previous_status=current,
                    new_status=target,
                    payload={
                        "reason_code": error_code,
                        "artifact_keys": list(artifact_keys),
                        "attempt_count": row.attempt_count,
                    },
                    now=now,
                )
                if target in {
                    ResearchNodeStatus.FAILED_TERMINAL,
                    ResearchNodeStatus.BLOCKED,
                    ResearchNodeStatus.CANCELLED,
                }:
                    self._block_descendants(session, job_id, row, now)
                session.flush()
                result = self._to_node(row)
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    def record_budget_stop(self, *, job_id: uuid.UUID, node_key: str, reason_code: str) -> None:
        """记录 P07-06 的停止原因；不改变节点状态，也不执行补证。"""
        if reason_code not in _BUDGET_REASON_CODES:
            raise ValueError("不支持的年度补证停止原因码")
        with self._sf() as session:
            try:
                row = self._require_row(session, job_id, node_key)
                self._append_event(
                    session,
                    row=row,
                    event_type=AnnualNodeEventType.BUDGET_STOP,
                    previous_status=ResearchNodeStatus(row.status),
                    new_status=ResearchNodeStatus(row.status),
                    payload={"reason_code": reason_code, "attempt_count": row.attempt_count},
                    now=_utc_now(),
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

    def recover_stale(
        self, *, stale_after: timedelta = timedelta(minutes=10)
    ) -> tuple[ResearchNode, ...]:
        """收口超过阈值的 running 节点；成功节点永不回退。"""
        cutoff = _utc_now() - stale_after
        recovered: list[ResearchNode] = []
        with self._sf() as session:
            try:
                candidates = (
                    session.execute(
                        select(AnnualResearchNode).where(
                            AnnualResearchNode.status == ResearchNodeStatus.RUNNING.value,
                            AnnualResearchNode.started_at.is_not(None),
                        )
                    )
                    .scalars()
                    .all()
                )
                # SQLite 和历史数据可能以 naive 形式返回时间；统一到 Python 侧再比较，
                # 避免数据库方言对 aware/naive 参数的不同序列化导致漏恢复。
                stale_rows = [
                    row
                    for row in candidates
                    if row.started_at is not None and _as_utc(row.started_at) < cutoff
                ]
                now = _utc_now()
                for row in stale_rows:
                    target = (
                        ResearchNodeStatus.FAILED_RETRYABLE
                        if row.attempt_count < row.max_attempts
                        else ResearchNodeStatus.FAILED_TERMINAL
                    )
                    previous = ResearchNodeStatus(row.status)
                    row.status = target.value
                    row.error_code = "STALE_NODE_TIMEOUT"
                    if target.is_terminal:
                        row.completed_at = now
                    self._append_event(
                        session,
                        row=row,
                        event_type=AnnualNodeEventType.STATE_CHANGED,
                        previous_status=previous,
                        new_status=target,
                        payload={
                            "reason_code": "STALE_NODE_TIMEOUT",
                            "attempt_count": row.attempt_count,
                        },
                        now=now,
                    )
                    self._append_event(
                        session,
                        row=row,
                        event_type=AnnualNodeEventType.RECOVERED,
                        previous_status=previous,
                        new_status=target,
                        payload={
                            "reason_code": "STALE_NODE_TIMEOUT",
                            "attempt_count": row.attempt_count,
                        },
                        now=now,
                    )
                    if target is ResearchNodeStatus.FAILED_TERMINAL:
                        self._block_descendants(session, row.job_id, row, now)
                    recovered.append(self._to_node(row))
                session.flush()
                session.commit()
                return tuple(recovered)
            except Exception:
                session.rollback()
                raise

    def snapshot(self, *, job_id: uuid.UUID) -> AnnualNodeGraphSnapshot:
        """返回可解释的节点、等待原因、最近事件和关键路径读模型。"""
        with self._sf() as session:
            return self._snapshot(session, job_id)

    def runnable_nodes(self, *, job_id: uuid.UUID) -> tuple[ResearchNode, ...]:
        """返回依赖全部成功且尚未运行的节点，不修改任何状态。"""
        with self._sf() as session:
            rows = self._rows_by_key(session, job_id)
            edges = self._edges_by_key(session, job_id, rows)
            upstreams: dict[str, set[str]] = defaultdict(set)
            for edge in edges:
                upstreams[edge.downstream_node_key].add(edge.upstream_node_key)
            return tuple(
                self._to_node(row)
                for key, row in rows.items()
                if ResearchNodeStatus(row.status)
                in {ResearchNodeStatus.PENDING, ResearchNodeStatus.FAILED_RETRYABLE}
                and all(
                    ResearchNodeStatus(rows[item].status) is ResearchNodeStatus.SUCCEEDED
                    for item in upstreams[key]
                )
            )

    def _snapshot(self, session: Any, job_id: uuid.UUID) -> AnnualNodeGraphSnapshot:
        rows = self._rows_by_key(session, job_id)
        edges = self._edges_by_key(session, job_id, rows)
        waits = self._waits(rows, edges)
        node_ids = [row.id for row in rows.values()]
        events = (
            session.execute(
                select(AnnualNodeEvent)
                .where(AnnualNodeEvent.node_id.in_(node_ids))
                .order_by(AnnualNodeEvent.created_at.desc(), AnnualNodeEvent.event_no.desc())
                .limit(50)
            )
            .scalars()
            .all()
            if node_ids
            else []
        )
        keys_by_id = {row.id: key for key, row in rows.items()}
        event_snapshots = tuple(
            self._to_event_snapshot(event, keys_by_id[event.node_id]) for event in reversed(events)
        )
        # 停止原因是"结果事实"，不能依赖最近事件窗口（limit 50 会把早期停止原因挤出
        # 快照）；单独全量查询 BUDGET_STOP 事件。recent_events 仍保留最近窗口语义。
        budget_events = (
            session.execute(
                select(AnnualNodeEvent)
                .where(
                    AnnualNodeEvent.node_id.in_(node_ids),
                    AnnualNodeEvent.event_type == AnnualNodeEventType.BUDGET_STOP.value,
                )
                .order_by(AnnualNodeEvent.created_at.asc(), AnnualNodeEvent.event_no.asc())
            )
            .scalars()
            .all()
            if node_ids
            else []
        )
        budget_reasons = tuple(
            event.payload.get("reason_code")
            for event in budget_events
            if event.payload.get("reason_code") is not None
        )
        path, seconds = self._critical_path(rows, edges)
        return AnnualNodeGraphSnapshot(
            job_id=str(job_id),
            nodes=tuple(self._to_node(row) for row in rows.values()),
            waiting=waits,
            recent_events=event_snapshots,
            budget_stop_reasons=budget_reasons,
            critical_path_node_keys=path,
            critical_path_seconds=seconds,
        )

    @staticmethod
    def _validate_graph_input(
        nodes: tuple[ResearchNode, ...], dependencies: tuple[NodeDependency, ...]
    ) -> None:
        keys = tuple(node.node_key for node in nodes)
        if not keys:
            raise AnnualNodeGraphError("年度节点图至少需要一个节点")
        if len(keys) != len(set(keys)):
            raise AnnualNodeGraphError("年度节点图不能包含重复 node_key")
        key_set = set(keys)
        for edge in dependencies:
            if edge.upstream_node_key not in key_set or edge.downstream_node_key not in key_set:
                raise AnnualNodeGraphError("依赖边两端必须在同一年度节点图中")
        if len(set(dependencies)) != len(dependencies):
            raise AnnualNodeGraphError("年度节点图不能包含重复依赖边")
        SqlAnnualNodeStore._assert_acyclic(keys, dependencies)

    @staticmethod
    def _assert_acyclic(node_keys: tuple[str, ...], edges: tuple[NodeDependency, ...]) -> None:
        children: dict[str, set[str]] = {key: set() for key in node_keys}
        for edge in edges:
            children[edge.upstream_node_key].add(edge.downstream_node_key)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise AnnualNodeGraphError("年度节点依赖图存在环")
            if key in visited:
                return
            visiting.add(key)
            for child in children[key]:
                visit(child)
            visiting.remove(key)
            visited.add(key)

        for key in node_keys:
            visit(key)

    def _assert_same_graph(
        self,
        existing: list[AnnualResearchNode],
        session: Any,
        job_id: uuid.UUID,
        requested_nodes: tuple[ResearchNode, ...],
        requested_edges: tuple[NodeDependency, ...],
    ) -> None:
        by_key = {row.node_key: row for row in existing}
        if set(by_key) != {node.node_key for node in requested_nodes}:
            raise AnnualNodeGraphError("同一 job 已存在不同的年度节点图")
        for node in requested_nodes:
            row = by_key[node.node_key]
            if (
                row.node_kind != node.kind.value
                or row.max_attempts != node.max_attempts
                or row.input_fingerprint != node.input_fingerprint
            ):
                raise AnnualNodeGraphError("同一 job 的年度节点定义不能被覆盖")
        if set(self._edges_by_key(session, job_id, by_key)) != set(requested_edges):
            raise AnnualNodeGraphError("同一 job 的年度节点依赖图不能被覆盖")

    @staticmethod
    def _to_node(row: AnnualResearchNode) -> ResearchNode:
        return ResearchNode(
            node_key=row.node_key,
            kind=row.node_kind,
            status=row.status,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            input_fingerprint=row.input_fingerprint,
            output_artifact_keys=tuple(row.output_artifact_keys or []),
            blocked_reason=row.blocked_reason,
            error_code=row.error_code,
        )

    @staticmethod
    def _rows_by_key(session: Any, job_id: uuid.UUID) -> dict[str, AnnualResearchNode]:
        return {
            row.node_key: row
            for row in session.execute(
                select(AnnualResearchNode)
                .where(AnnualResearchNode.job_id == job_id)
                .order_by(AnnualResearchNode.node_key.asc())
            ).scalars()
        }

    @staticmethod
    def _edges_by_key(
        session: Any, job_id: uuid.UUID, rows: dict[str, AnnualResearchNode]
    ) -> tuple[NodeDependency, ...]:
        keys_by_id = {row.id: key for key, row in rows.items()}
        return tuple(
            NodeDependency(
                upstream_node_key=keys_by_id[row.upstream_node_id],
                downstream_node_key=keys_by_id[row.downstream_node_id],
            )
            for row in session.execute(
                select(AnnualNodeDependency).where(AnnualNodeDependency.job_id == job_id)
            ).scalars()
        )

    @staticmethod
    def _require_row(session: Any, job_id: uuid.UUID, node_key: str) -> AnnualResearchNode:
        row = session.execute(
            select(AnnualResearchNode).where(
                AnnualResearchNode.job_id == job_id, AnnualResearchNode.node_key == node_key
            )
        ).scalar_one_or_none()
        if row is None:
            raise AnnualNodeGraphError("年度节点不存在或不属于指定 job")
        return cast(AnnualResearchNode, row)

    def _append_event(
        self,
        session: Any,
        *,
        row: AnnualResearchNode,
        event_type: AnnualNodeEventType,
        previous_status: ResearchNodeStatus | None,
        new_status: ResearchNodeStatus | None,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
        sanitized = self._sanitize_event_payload(payload)
        # Worker 使用 autoflush=False；同一事务可能连续追加状态和恢复事件。
        # 先让待写事件对 MAX(event_no) 可见，避免分配重复序号；不提前提交事务。
        session.flush()
        next_no = (
            session.execute(
                select(func.coalesce(func.max(AnnualNodeEvent.event_no), 0)).where(
                    AnnualNodeEvent.node_id == row.id
                )
            ).scalar_one()
            + 1
        )
        session.add(
            AnnualNodeEvent(
                job_id=row.job_id,
                node_id=row.id,
                event_no=next_no,
                event_type=event_type.value,
                previous_status=previous_status.value if previous_status else None,
                new_status=new_status.value if new_status else None,
                payload=sanitized,
                created_at=now,
            )
        )

    @staticmethod
    def _sanitize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
        unknown = set(payload) - _EVENT_PAYLOAD_KEYS
        if unknown:
            raise ValueError("年度节点事件载荷包含不允许字段")
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if value is None:
                continue
            if key == "artifact_keys":
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise ValueError("artifact_keys 必须是字符串列表")
            elif not isinstance(value, (str, int, float, bool)):
                raise ValueError("年度节点事件载荷只能包含脱敏标量")
            result[key] = value
        return result

    def _block_descendants(
        self, session: Any, job_id: uuid.UUID, upstream: AnnualResearchNode, now: datetime
    ) -> None:
        queue = [upstream]
        while queue:
            source = queue.pop()
            deps = (
                session.execute(
                    select(AnnualNodeDependency).where(
                        AnnualNodeDependency.job_id == job_id,
                        AnnualNodeDependency.upstream_node_id == source.id,
                    )
                )
                .scalars()
                .all()
            )
            for dependency in deps:
                child = session.get(AnnualResearchNode, dependency.downstream_node_id)
                if child is None or ResearchNodeStatus(child.status).is_terminal:
                    continue
                previous = ResearchNodeStatus(child.status)
                child.status = ResearchNodeStatus.BLOCKED.value
                child.blocked_reason = f"upstream_{source.node_key}_{source.status}"
                child.completed_at = now
                self._append_event(
                    session,
                    row=child,
                    event_type=AnnualNodeEventType.STATE_CHANGED,
                    previous_status=previous,
                    new_status=ResearchNodeStatus.BLOCKED,
                    payload={
                        "reason_code": "upstream_terminal_failure",
                        "attempt_count": child.attempt_count,
                    },
                    now=now,
                )
                queue.append(child)

    @staticmethod
    def _waits(
        rows: dict[str, AnnualResearchNode], edges: tuple[NodeDependency, ...]
    ) -> tuple[AnnualNodeWait, ...]:
        upstreams: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            upstreams[edge.downstream_node_key].append(edge.upstream_node_key)
        waits: list[AnnualNodeWait] = []
        for key, row in rows.items():
            if ResearchNodeStatus(row.status) not in {
                ResearchNodeStatus.PENDING,
                ResearchNodeStatus.FAILED_RETRYABLE,
            }:
                continue
            for upstream_key in upstreams[key]:
                upstream = rows[upstream_key]
                status = ResearchNodeStatus(upstream.status)
                if status is not ResearchNodeStatus.SUCCEEDED:
                    waits.append(
                        AnnualNodeWait(
                            node_key=key,
                            upstream_node_key=upstream_key,
                            upstream_status=status,
                            reason_code=f"upstream_{status.value}",
                        )
                    )
        return tuple(waits)

    @staticmethod
    def _to_event_snapshot(event: AnnualNodeEvent, node_key: str) -> AnnualNodeEventSnapshot:
        payload = event.payload or {}
        return AnnualNodeEventSnapshot(
            node_key=node_key,
            event_no=event.event_no,
            event_type=event.event_type,
            previous_status=event.previous_status,
            new_status=event.new_status,
            reason_code=payload.get("reason_code"),
            artifact_keys=tuple(payload.get("artifact_keys", [])),
            attempt_count=int(payload.get("attempt_count", 0)),
            created_at=_as_utc(event.created_at) if event.created_at is not None else _utc_now(),
        )

    @staticmethod
    def _critical_path(
        rows: dict[str, AnnualResearchNode], edges: tuple[NodeDependency, ...]
    ) -> tuple[tuple[str, ...], float]:
        upstreams: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            upstreams[edge.downstream_node_key].append(edge.upstream_node_key)
        memo: dict[str, tuple[tuple[str, ...], float]] = {}

        def elapsed(row: AnnualResearchNode) -> float:
            if row.started_at is None:
                return 0.0
            start = _as_utc(row.started_at)
            end = _as_utc(row.completed_at) if row.completed_at is not None else _utc_now()
            return max((end - start).total_seconds(), 0.0)

        def longest(key: str) -> tuple[tuple[str, ...], float]:
            if key in memo:
                return memo[key]
            previous = [longest(item) for item in upstreams[key]]
            prefix, total = max(previous, key=lambda item: item[1]) if previous else ((), 0.0)
            memo[key] = (prefix + (key,), total + elapsed(rows[key]))
            return memo[key]

        return max((longest(key) for key in rows), key=lambda item: item[1], default=((), 0.0))
