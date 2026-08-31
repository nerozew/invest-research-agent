"""P07-10 年度 DAG 节点的持久化状态机与诊断契约。

本模块保持纯领域：不导入 SQLAlchemy、Prometheus 或 OpenTelemetry。它与旧
``workflow_steps`` / ``StepStatus`` 完全独立，供年度节点存储和未来调度器共用。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.domain.annual_pipeline import ResearchNode, ResearchNodeStatus


class AnnualNodeEventType(StrEnum):
    """年度节点追加式事件的稳定类别。"""

    NODE_CREATED = "node_created"
    STATE_CHANGED = "state_changed"
    WAITING = "waiting"
    BUDGET_STOP = "budget_stop"
    RECOVERED = "recovered"


class AnnualNodeTransitionError(ValueError):
    """请求的年度节点状态转换不在状态机允许范围内。"""


_TRANSITIONS: dict[ResearchNodeStatus, frozenset[ResearchNodeStatus]] = {
    ResearchNodeStatus.PENDING: frozenset(
        {ResearchNodeStatus.RUNNING, ResearchNodeStatus.BLOCKED, ResearchNodeStatus.CANCELLED}
    ),
    ResearchNodeStatus.RUNNING: frozenset(
        {
            ResearchNodeStatus.SUCCEEDED,
            ResearchNodeStatus.FAILED_RETRYABLE,
            ResearchNodeStatus.FAILED_TERMINAL,
            ResearchNodeStatus.BLOCKED,
            ResearchNodeStatus.CANCELLED,
        }
    ),
    ResearchNodeStatus.FAILED_RETRYABLE: frozenset(
        {
            ResearchNodeStatus.RUNNING,
            ResearchNodeStatus.FAILED_TERMINAL,
            ResearchNodeStatus.BLOCKED,
            ResearchNodeStatus.CANCELLED,
        }
    ),
    ResearchNodeStatus.SUCCEEDED: frozenset(),
    ResearchNodeStatus.FAILED_TERMINAL: frozenset(),
    ResearchNodeStatus.BLOCKED: frozenset(),
    ResearchNodeStatus.CANCELLED: frozenset(),
}


def can_transition_annual_node(current: ResearchNodeStatus, target: ResearchNodeStatus) -> bool:
    """返回年度节点状态机是否允许该次转换。"""
    return target in _TRANSITIONS[current]


def transition_annual_node(
    current: ResearchNodeStatus, target: ResearchNodeStatus
) -> ResearchNodeStatus:
    """校验并返回目标状态；终态节点绝不可回退。"""
    if not can_transition_annual_node(current, target):
        raise AnnualNodeTransitionError(f"非法年度节点状态转换: {current.value} -> {target.value}")
    return target


class AnnualNodeEventSnapshot(BaseModel):
    """经过字段白名单过滤后的单条节点事件。"""

    model_config = ConfigDict(frozen=True)

    node_key: str = Field(min_length=1, max_length=200)
    event_no: int = Field(ge=1)
    event_type: AnnualNodeEventType
    previous_status: ResearchNodeStatus | None = None
    new_status: ResearchNodeStatus | None = None
    reason_code: str | None = None
    artifact_keys: tuple[str, ...] = ()
    attempt_count: int = Field(ge=0)
    created_at: datetime


class AnnualNodeWait(BaseModel):
    """节点尚不能运行时的一个确定性上游原因。"""

    model_config = ConfigDict(frozen=True)

    node_key: str = Field(min_length=1, max_length=200)
    upstream_node_key: str = Field(min_length=1, max_length=200)
    upstream_status: ResearchNodeStatus
    reason_code: str = Field(min_length=1, max_length=1_000)


class AnnualNodeGraphSnapshot(BaseModel):
    """供诊断界面读取的年度 DAG 快照；不含原始 SEC 内容或提示词。"""

    model_config = ConfigDict(frozen=True)

    job_id: str = Field(min_length=1)
    nodes: tuple[ResearchNode, ...]
    waiting: tuple[AnnualNodeWait, ...] = ()
    recent_events: tuple[AnnualNodeEventSnapshot, ...] = ()
    budget_stop_reasons: tuple[str, ...] = ()
    critical_path_node_keys: tuple[str, ...] = ()
    critical_path_seconds: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _no_duplicate_nodes(self) -> "AnnualNodeGraphSnapshot":
        keys = [node.node_key for node in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("年度节点图快照不能包含重复 node_key")
        return self
