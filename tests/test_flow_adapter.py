"""P04-07：Flow adapter 集成测试。

- ``ResearchFlowRunner`` 包装真实 ResearchFlow（fake 逻辑，P03 已验证 00-07 全链）；
  通过 ``last_state`` 强断言：Flow 推进到 quality_report（质量门禁）与 run_manifest（发布）。
- ``ResearchJobExecutionHandler`` 委托 ExecuteResearchJobService；
  用 fake service 验证 job_id 被转发。
"""

from __future__ import annotations

import uuid
from datetime import date

from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.queue.flow_adapter import (
    ResearchFlowRunner,
    ResearchJobExecutionHandler,
)


def _sample_request() -> ResearchRequest:
    return ResearchRequest(
        input_company="Microsoft",
        as_of_date=date(2026, 7, 31),
        language="zh-CN",
        requested_forms=("10-K", "10-Q"),
    )


def test_flow_runner_advances_state_to_quality_and_manifest() -> None:
    """ResearchFlowRunner.run 驱动 fake Flow 至 06（质量门禁）与 07（发布 manifest）。"""
    runner = ResearchFlowRunner()
    runner.run(_sample_request())

    # state 已推进到 06：quality_report 已生成
    assert runner.last_state is not None
    assert runner.last_state.quality_report is not None
    # 07：run_manifest 已生成（发布/拒绝均有记录）
    assert runner.last_state.run_manifest is not None
    assert "status" in runner.last_state.run_manifest


def test_job_handler_forwards_job_id() -> None:
    """ResearchJobExecutionHandler.process 把 job_id 转发给执行服务。"""

    class FakeService:
        def __init__(self) -> None:
            self.processed: list[uuid.UUID] = []

        def process(self, job_id: uuid.UUID) -> None:
            self.processed.append(job_id)

    service = FakeService()
    handler = ResearchJobExecutionHandler(service)
    job_id = uuid.uuid4()

    handler.process(job_id)

    assert service.processed == [job_id]
