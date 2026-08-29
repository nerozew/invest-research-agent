"""P07-10 年度节点的低基数指标与脱敏 span 工具。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from invest_research.infrastructure.observability.metrics import (
    annual_budget_stops_total,
    annual_node_duration_seconds,
    annual_node_transitions_total,
    annual_nodes_waiting,
)
from invest_research.infrastructure.observability.tracing import span

__all__ = [
    "record_annual_budget_stop",
    "record_annual_node_transition",
    "record_annual_waiting",
    "annual_node_span",
]


def record_annual_node_transition(
    *, node_kind: str, from_status: str, to_status: str, duration_seconds: float | None = None
) -> None:
    """记录节点转换；标签只允许节点类别和状态，绝不含 job/node/company。"""
    annual_node_transitions_total.labels(
        node_kind=node_kind, from_status=from_status, to_status=to_status
    ).inc()
    if duration_seconds is not None:
        annual_node_duration_seconds.labels(node_kind=node_kind, status=to_status).observe(
            max(duration_seconds, 0.0)
        )


def record_annual_waiting(*, reason_code: str, count: int) -> None:
    """覆盖某个稳定等待原因下的节点数。"""
    annual_nodes_waiting.labels(reason_code=reason_code).set(max(count, 0))


def record_annual_budget_stop(*, reason_code: str) -> None:
    """记录 P07-06 预算/无增益等停止原因。"""
    annual_budget_stops_total.labels(reason_code=reason_code).inc()


@contextmanager
def annual_node_span(
    *,
    operation: str,
    node_kind: str,
    status: str,
    attempt_count: int,
    error_code: str | None = None,
) -> Iterator[object]:
    """创建年度执行或恢复 span，只写脱敏、低基数的诊断字段。"""
    attributes: dict[str, str | int] = {
        "annual.node.operation": operation,
        "annual.node.kind": node_kind,
        "annual.node.status": status,
        "annual.node.attempt_count": attempt_count,
    }
    if error_code:
        attributes["annual.node.error_code"] = error_code
    with span(f"annual_node.{operation}", attributes) as current:
        yield current
