"""Worker → Flow 的基础设施 adapter（P04-07）。

把两层桥接起来：
- ``ResearchFlowRunner``：实现 ``application.execution.FlowRunner`` 端口，
  委托 ``ResearchFlow.run_fake(request)``（纯 fake 逻辑 Flow，P03 已验证）。
  执行后把最终 ``state`` 记录到 ``last_state``，供测试断言与审计展示。
- ``ResearchJobExecutionHandler``：实现 ``infrastructure.queue.tasks.JobTaskHandler``
  端口，把 worker 收到的 job_id 委派给 ``ExecuteResearchJobService``
  （pending→running→终态 + 防重复执行）。

依赖方向：infrastructure -> application（端口/用例）+ flows（Flow 实体）。
本模块不直接写 SQL；从 Repository 加载/更新状态由注入的端口完成。
"""

from __future__ import annotations

import uuid
from typing import Callable

from invest_research.application.execution import (
    ExecuteResearchJobService,
)
from invest_research.application.progress import ProgressSink
from invest_research.domain.models import ResearchRequest
from invest_research.flows.research_flow import ResearchFlow
from invest_research.flows.state import ResearchFlowState

__all__ = ["ResearchFlowRunner", "ResearchJobExecutionHandler"]


class ResearchFlowRunner:
    """把 ResearchFlow 包装为 FlowRunner 端口。

    ``last_state``：最后一次 ``run`` 执行后的 Flow state
    （P04-07 验收：state 从 pending 推进到 quality_report/run_manifest）。

    P06-06B：``progress`` / ``job_id`` 由 Worker 构建 handler 时注入（可选）；
    提供时在 Flow 真实步骤边界标记实时进度（不传则行为与旧版一致）。
    """

    def __init__(self) -> None:
        # 每个 job 新建 Flow 实例（Flow 是有状态工作台，不跨任务复用）。
        self._flow = ResearchFlow()
        self.last_state: ResearchFlowState | None = None
        # P06-06B：Worker 在开始处理任务前注入的实时进度端口与 job_id
        self.progress: ProgressSink | None = None
        self.job_id: uuid.UUID | None = None

    def run(self, request: ResearchRequest) -> ResearchFlowState:
        # P06-06B：把 Worker 注入的 job_id/progress 传给 Flow 步骤边界钩子
        self.last_state = self._flow.run_fake(
            request,
            job_id=self.job_id,
            progress=self.progress,
        )
        # P06-07 前置修复：FlowRunner 端口返回最终 state（Worker 发布最终报告用）
        return self.last_state


class ResearchJobExecutionHandler:
    """Celery worker 侧的 job handler：委托执行服务。

    ``recorder``：可选执行后回调（P05.5-opt），成功执行后把步骤/工件/耗时落库；
    记录失败不影响任务本身（由调用方兜底）。
    """

    def __init__(
        self,
        service: ExecuteResearchJobService,
        recorder: Callable[[uuid.UUID], None] | None = None,
    ) -> None:
        self._service = service
        self._recorder = recorder

    def process(self, job_id: uuid.UUID) -> None:
        self._service.process(job_id)
        if self._recorder is not None:
            self._recorder(job_id)
