"""取消任务用例与端口（P04-08：协作式取消）。

语义（对齐 docs/04 §3 状态机 + .clinerules 02）：
- ``pending``／``running`` 可取消 → ``cancelled``；
- 终态（succeeded/partial/failed/cancelled）不可取消；
- 重复取消保持幂等：已 cancelled 的任务再次 cancel 不报错、不改变状态（返回 already）。

端口：
- ``CancelStatusWriter``：把任务从 pending→cancelled 或 running→cancelled 的条件更新。
  只有"当前状态真的等于前置状态"才返回 True（乐观锁语义，防并发覆盖）。
- 本层不导入 SQLAlchemy/Celery/CrewAI；测试注入 fake writer 完全离线。
"""

from __future__ import annotations

import uuid
from typing import Protocol

from invest_research.domain.status import JobStatus
from invest_research.domain.transitions import InvalidStateTransitionError

__all__ = ["CancelOutcome", "CancelResearchJobService", "CancelStatusWriter"]

# 取消结果：did_cancel = 本次真正把 pending/running 取消为 cancelled；
# already_cancelled = 任务已是终态或已取消（幂等无变化）。
CancelOutcome = tuple[bool, bool]


class CancelStatusWriter(Protocol):
    """把任务从某状态条件更新为 cancelled 的端口。"""

    def cancel_from_pending(self, job_id: uuid.UUID) -> bool: ...
    def cancel_from_running(self, job_id: uuid.UUID) -> bool: ...


class CancelResearchJobService:
    """取消研究任务用例（P04-08）。

    ``cancel(job_id)`` 语义：
    1. 先尝试 pending→cancelled（乐观锁：只有当前确实 pending 才成功）；
    2. 若失败（任务不是 pending），尝试 running→cancelled；
    3. 两者都失败 → 任务已是终态（含已 cancelled）→ 幂等返回；
    4. 状态合法性由 domain.transitions 背书（pending/running 是仅有的
       两个能迁移到 cancelled 的前置状态）。
    """

    def __init__(self, writer: CancelStatusWriter) -> None:
        self._writer = writer

    def cancel(self, job_id: uuid.UUID) -> CancelOutcome:
        # pending → cancelled（第一个安全点：任务尚未开始执行）
        if self._writer.cancel_from_pending(job_id):
            return (True, False)
        # running → cancelled（第二个安全点：任务执行中被协作式停止）
        if self._writer.cancel_from_running(job_id):
            return (True, False)
        # 两个前置状态都不匹配 → 终态（含已 cancelled）：幂等，无变化
        return (False, True)

    @staticmethod
    def assert_cancellable(current: JobStatus) -> None:
        """fail-fast 校验：只有不可取消的状态才抛错。

        - pending/running：可取消；
        - cancelled：重复取消是幂等合法操作（不抛错）；
        - succeeded/partial/failed：根本不允许取消（抛错提示）。
        """
        non_cancellable = {
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
        }
        if current in non_cancellable:
            raise InvalidStateTransitionError("job", current, JobStatus.CANCELLED)
