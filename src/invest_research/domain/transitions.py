"""纯函数 Job/Step 状态转换器（P01-06）。

在复用 `domain/status.py` 的 `can_transition_job/can_transition_step`
规则表基础上，向上提供"执行转换"的服务：
- 合法转换：返回目标状态；
- 非法转换：抛出 `InvalidStateTransitionError`（fail-fast），
  异常中记录对象类型（job/step）、当前状态与目标状态。

依据 docs/04-WORKFLOW-RELIABILITY.md §3 状态机。
本模块为纯 Python 领域层，不访问数据库，不引入任何外部框架。
"""

from __future__ import annotations

from typing import Literal

from invest_research.domain.status import (
    JobStatus,
    StepStatus,
    can_transition_job,
    can_transition_step,
)

StateObjectType = Literal["job", "step"]


class InvalidStateTransitionError(ValueError):
    """状态转换非法（不允许的迁移）。

    记录对象类型、当前状态与目标状态，便于上层精确处理与排障。
    """

    def __init__(
        self,
        object_type: StateObjectType,
        current: JobStatus | StepStatus,
        target: JobStatus | StepStatus,
    ) -> None:
        self.object_type = object_type
        self.current = current
        self.target = target
        super().__init__(f"非法{object_type}状态转换: {current.value} -> {target.value}")


def transition_job(current: JobStatus, target: JobStatus) -> JobStatus:
    """执行 Job 状态转换。

    复用 can_transition_job 判断合法性：
    - 合法：返回 target；
    - 非法：抛 InvalidStateTransitionError。
    """
    if not can_transition_job(current, target):
        raise InvalidStateTransitionError("job", current, target)
    return target


def transition_step(current: StepStatus, target: StepStatus) -> StepStatus:
    """执行 Step 状态转换。

    复用 can_transition_step 判断合法性：
    - 合法：返回 target；
    - 非法：抛 InvalidStateTransitionError。
    """
    if not can_transition_step(current, target):
        raise InvalidStateTransitionError("step", current, target)
    return target
