"""P06-06B：Worker 运行中步骤进度端口（ProgressSink）。

职责：
- 在 Worker/Flow 真实执行边界更新 workflow_steps 的实时状态（不解析日志文本）；
- 幂等创建 00-07 步骤；重复 Celery 投递不得重复插入（复用 (job_id, step_name) 唯一约束）；
- 只允许合法状态转换（对齐 docs/04 §3 的 StepStatus 状态机）；
- 设置/清空 research_jobs.current_step；
- 端口由 infrastructure 提供真实 SQL 实现；Flow/Agent 不直接 import SQLAlchemy。

短事务：实现者每个操作独立短事务，保证进度写入失败不阻塞任务本身。
"""

from __future__ import annotations

import uuid
from typing import Protocol

# 工作流步骤 00-07（P06-06B；与 ExecutionRecorder/_STEP_SPECS 对齐）。
STEP_NAMES: tuple[str, ...] = (
    "00_request",
    "01_company_resolve",
    "02_research",
    "03_documents",
    "04_analysis",
    "05_writer",
    "06_quality_gate",
    "07_manifest",
)

# 步骤名 → sequence_no（与 db models/workflow_steps.sequence_no 对齐）。
STEP_SEQUENCE: dict[str, int] = {name: idx for idx, name in enumerate(STEP_NAMES)}


class StepRecordError(RuntimeError):
    """步骤进度记录失败（应用边界：调用方记录脱敏日志，不误报任务失败）。"""


class ProgressSink(Protocol):
    """Worker/Flow 实时步骤进度端口。

    实现者要求：
    - 每个方法使用独立短事务；
    - 幂等创建步骤（同 job 重复投递不重复插入）；
    - 状态转换必须合法（应用层调用方保证，实现者可做防护性检查）；
    - 任何写入失败抛 ``StepRecordError``，由调用方记录脱敏日志后继续任务。
    """

    def initialize_steps(self, job_id: uuid.UUID) -> None:
        """幂等创建 00-07 步骤，初始状态 pending。

        重复调用不得重复插入（依靠 ``uq_workflow_steps_job_step`` 唯一约束）。
        """
        ...

    def mark_step_running(self, job_id: uuid.UUID, step_name: str) -> None:
        """步骤开始：合法条件（pending/failed_retryable → running）更新，
        写 started_at，并更新 research_jobs.current_step。
        """
        ...

    def mark_step_succeeded(self, job_id: uuid.UUID, step_name: str) -> None:
        """步骤成功：running → succeeded，写 completed_at，保留 attempt_count。"""
        ...

    def mark_step_failed(
        self,
        job_id: uuid.UUID,
        step_name: str,
        *,
        error_code: str,
        error_message: str,
        terminal: bool,
    ) -> None:
        """步骤失败：running → failed_retryable/failed_terminal，保存脱敏错误摘要。

        terminal=True 时写 failed_terminal；否则写 failed_retryable。
        """
        ...

    def fail_all_running_steps(
        self,
        job_id: uuid.UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        """Job 失败时把所有仍为 running 的步骤收口为 failed_terminal 并清空 current_step。"""
        ...

    def clear_current_step(self, job_id: uuid.UUID) -> None:
        """Job 进入终态时清空 research_jobs.current_step。"""
        ...
