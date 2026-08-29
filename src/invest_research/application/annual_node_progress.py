"""P07-10 年度节点进度服务端口。

服务只协调持久化状态与诊断读取；它不连接 Flow、Celery、API 或年度运行时。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Protocol

from invest_research.domain.annual_node_runtime import AnnualNodeGraphSnapshot
from invest_research.domain.annual_pipeline import NodeDependency, ResearchNode, ResearchNodeStatus

__all__ = ["AnnualNodeProgressService", "AnnualNodeStore"]


class AnnualNodeStore(Protocol):
    """P07-10 存储端口；未来调度器可用 SQL 或测试替身实现。"""

    def create_graph(
        self,
        *,
        job_id: uuid.UUID,
        nodes: tuple[ResearchNode, ...],
        dependencies: tuple[NodeDependency, ...] = (),
    ) -> AnnualNodeGraphSnapshot: ...

    def transition(
        self,
        *,
        job_id: uuid.UUID,
        node_key: str,
        target: ResearchNodeStatus,
        error_code: str | None = None,
        blocked_reason: str | None = None,
        artifact_keys: tuple[str, ...] = (),
    ) -> ResearchNode: ...

    def record_budget_stop(self, *, job_id: uuid.UUID, node_key: str, reason_code: str) -> None: ...

    def recover_stale(
        self, *, stale_after: timedelta = timedelta(minutes=10)
    ) -> tuple[ResearchNode, ...]: ...

    def snapshot(self, *, job_id: uuid.UUID) -> AnnualNodeGraphSnapshot: ...

    def runnable_nodes(self, *, job_id: uuid.UUID) -> tuple[ResearchNode, ...]: ...


class AnnualNodeProgressService:
    """年度图的窄应用服务，默认僵尸节点恢复阈值为十分钟。"""

    def __init__(self, store: AnnualNodeStore) -> None:
        self._store = store

    def create_graph(
        self,
        *,
        job_id: uuid.UUID,
        nodes: tuple[ResearchNode, ...],
        dependencies: tuple[NodeDependency, ...] = (),
    ) -> AnnualNodeGraphSnapshot:
        return self._store.create_graph(job_id=job_id, nodes=nodes, dependencies=dependencies)

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
        return self._store.transition(
            job_id=job_id,
            node_key=node_key,
            target=target,
            error_code=error_code,
            blocked_reason=blocked_reason,
            artifact_keys=artifact_keys,
        )

    def record_budget_stop(self, *, job_id: uuid.UUID, node_key: str, reason_code: str) -> None:
        self._store.record_budget_stop(job_id=job_id, node_key=node_key, reason_code=reason_code)

    def recover_stale(
        self, *, stale_after: timedelta = timedelta(minutes=10)
    ) -> tuple[ResearchNode, ...]:
        return self._store.recover_stale(stale_after=stale_after)

    def snapshot(self, *, job_id: uuid.UUID) -> AnnualNodeGraphSnapshot:
        return self._store.snapshot(job_id=job_id)

    def runnable_nodes(self, *, job_id: uuid.UUID) -> tuple[ResearchNode, ...]:
        return self._store.runnable_nodes(job_id=job_id)
