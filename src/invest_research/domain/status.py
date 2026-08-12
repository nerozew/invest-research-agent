"""Job / Step 状态枚举与状态转换规则（P01-01）。

依据：
- `docs/03-DATABASE.md` §3：`research_jobs.status` 与 `workflow_steps.status` 的合法值；
- `docs/04-WORKFLOW-RELIABILITY.md` §3：任务状态机与 Job 状态规则。

本模块为纯 Python，禁止导入任何外部库，保证可独立测试。
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    """研究任务的整体状态（research_jobs.status）。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """是否为终态（到达后不再迁移）。"""
        return self in {
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }


class StepStatus(StrEnum):
    """单个工作流步骤的状态（workflow_steps.status）。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        """是否为终态（到达后不再迁移）。"""
        return self in {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED_TERMINAL,
            StepStatus.SKIPPED,
        }


# ---------------------------------------------------------------------------
# Job 状态转换规则（docs/04-WORKFLOW-RELIABILITY.md §3）
# ---------------------------------------------------------------------------

JobTransitions: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.PARTIAL: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}

# ---------------------------------------------------------------------------
# Step 状态转换规则（docs/04-WORKFLOW-RELIABILITY.md §3 状态机）
# ---------------------------------------------------------------------------

StepTransitions: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.RUNNING, StepStatus.SKIPPED}),
    StepStatus.RUNNING: frozenset(
        {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED_RETRYABLE,
            StepStatus.FAILED_TERMINAL,
            StepStatus.SKIPPED,
        }
    ),
    StepStatus.FAILED_RETRYABLE: frozenset({StepStatus.RUNNING, StepStatus.FAILED_TERMINAL}),
    StepStatus.SUCCEEDED: frozenset(),
    StepStatus.FAILED_TERMINAL: frozenset(),
    StepStatus.SKIPPED: frozenset(),
}


def can_transition_job(current: JobStatus, target: JobStatus) -> bool:
    """判断 Job 状态迁移是否合法。非法迁移返回 False。"""
    return target in JobTransitions.get(current, frozenset())


def can_transition_step(current: StepStatus, target: StepStatus) -> bool:
    """判断 Step 状态迁移是否合法。非法迁移返回 False。"""
    return target in StepTransitions.get(current, frozenset())
