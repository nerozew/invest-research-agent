"""Worker → Flow 的基础设施 adapter（P04-07）。

把两层桥接起来：
- ``ResearchFlowRunner``：实现 ``application.execution.FlowRunner`` 端口，
  委托 ``ResearchFlow.run_fake(request)``（纯 fake 逻辑 Flow，P03 已验证）。
  执行后把最终 ``state`` 记录到 ``last_state``，供测试断言与审计展示。
- ``ResearchJobExecutionHandler``：实现 ``infrastructure.queue.tasks.JobTaskHandler``
  端口，把 worker 收到的 job_id 委托给 ``ExecuteResearchJobService``
  （pending→running→终态 + 防重复执行）。

依赖方向：infrastructure -> application（端口/用例）+ flows（Flow 实体）。
本模块不直接写 SQL；从 Repository 加载/更新状态由注入的端口完成。
"""

from __future__ import annotations

import uuid

from invest_research.application.execution import (
    ExecuteResearchJobService,
)
from invest_research.domain.models import ResearchRequest
from invest_research.flows.research_flow import ResearchFlow
from invest_research.flows.state import ResearchFlowState

__all__ = ["ResearchFlowRunner", "ResearchJobExecutionHandler"]


class ResearchFlowRunner:
    """把 ResearchFlow 包装为 FlowRunner 端口。

    ``last_state``：最后一次 ``run`` 执行后的 Flow state
    （P04-07 验收：state 从 pending 推进到 quality_report/run_manifest）。
    """

    def __init__(self) -> None:
        # 每个 job 新建 Flow 实例（Flow 是有状态工作台，不跨任务复用）。
        self._flow = ResearchFlow()
        self.last_state: ResearchFlowState | None = None

    def run(self, request: ResearchRequest) -> None:
        self.last_state = self._flow.run_fake(request)


class ResearchJobExecutionHandler:
    """Celery worker 侧的 job handler：委托执行服务。"""

    def __init__(self, service: ExecuteResearchJobService) -> None:
        self._service = service

    def process(self, job_id: uuid.UUID) -> None:
        self._service.process(job_id)
