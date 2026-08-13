"""Worker 执行服务（P04-07：Worker 调用 Flow）。

架构边界：
- Worker 只接收 ``job_id``；
- 通过端口（Protocol）从 Repository 加载请求、更新状态、运行 Flow；
- 本层不导入 SQLAlchemy/Celery/CrewAI——测试注入 fake 端口即可完全离线。

状态机（对齐 docs/04 §3）：
- ``mark_running`` 用"从 pending → running"的条件更新实现：仅当任务当前为
  pending 时成功（返回 True），否则返回 False——这同时保证：
  1. 正确处理 pending → running → 终态；
  2. 防止已完成的 job 被重复执行（幂等，乐观锁语义）。
- 成功完成 → ``mark_succeeded``（从 running → 终态 succeeded）。
"""

from __future__ import annotations

import uuid
from typing import Protocol

from invest_research.domain.models import ResearchRequest

__all__ = [
    "ExecutionStatusWriter",
    "ExecuteResearchJobService",
    "FlowRunner",
    "JobRequestLoader",
]


class JobRequestLoader(Protocol):
    """从存储加载任务的原始请求；不存在返回 None。"""

    def load(self, job_id: uuid.UUID) -> ResearchRequest | None: ...


class ExecutionStatusWriter(Protocol):
    """把任务状态从 pending 推进到 running 的条件更新端口。"""

    def mark_running(self, job_id: uuid.UUID) -> bool: ...
    def mark_succeeded(self, job_id: uuid.UUID) -> None: ...


class FlowRunner(Protocol):
    """执行研究 Flow 的端口（生产实现包装 ResearchFlow；测试用 fake）。"""

    def run(self, request: ResearchRequest) -> None: ...


class ExecuteResearchJobService:
    """执行一个投研任务的用例（P04-07）。

    ``process(job_id)`` 语义：
    1. ``mark_running`` 失败（任务不是 pending，可能已被其他 worker 处理/已终态）
       → 直接返回，绝不重复执行；
    2. 加载请求失败（任务已删除）→ 直接返回；
    3. 调用 Flow 运行；
    4. 成功 → ``mark_succeeded``（进入终态）。
    """

    def __init__(
        self,
        *,
        loader: JobRequestLoader,
        writer: ExecutionStatusWriter,
        flow_runner: FlowRunner,
    ) -> None:
        self._loader = loader
        self._writer = writer
        self._flow_runner = flow_runner

    def process(self, job_id: uuid.UUID) -> None:
        # 只有 pending 的任务才从 mark_running 拿到 True（防止重复执行/已完成任务）。
        if not self._writer.mark_running(job_id):
            return
        request = self._loader.load(job_id)
        if request is None:
            return
        self._flow_runner.run(request)
        self._writer.mark_succeeded(job_id)
