"""P04-07：Worker 调用 Flow 的执行服务测试。

- 用 fake Flow（FlowRunner 端口）验证 ExecuteResearchJobService：
  pending → running → succeeded 的终态推进；
- 验证"防止已完成任务重复执行"（mark_running 返回 False 时不运行 Flow）；
- 验证任务不存在时安全返回；
- 全程不依赖真实数据库/Redis/CrewAI（对齐"测试使用 fake Flow"）。

fake loader 提供 `.load(job_id)` 方法，与 JobRequestLoader 协议一致
（生产实现从 Repository 加载 ResearchRequest）。
"""

from __future__ import annotations

import uuid
from datetime import date

from invest_research.application.execution import ExecuteResearchJobService
from invest_research.domain.models import ResearchRequest


class FakeWriter:
    """fake 状态写入端口：记录 mark_running / mark_succeeded 调用。"""

    def __init__(self, running_can_proceed: bool = True) -> None:
        self.marked_running: list[uuid.UUID] = []
        self.marked_succeeded: list[uuid.UUID] = []
        self._can_proceed = running_can_proceed

    def mark_running(self, job_id: uuid.UUID) -> bool:
        self.marked_running.append(job_id)
        return self._can_proceed

    def mark_succeeded(self, job_id: uuid.UUID) -> None:
        self.marked_succeeded.append(job_id)


class FakeLoader:
    """fake 请求加载端口：返回预置请求或 None（任务不存在）。"""

    def __init__(self, request: ResearchRequest | None) -> None:
        self._request = request

    def load(self, job_id: uuid.UUID) -> ResearchRequest | None:
        return self._request


class FakeFlow:
    """fake Flow：记录收到的 request（不依赖 CrewAI）。"""

    def __init__(self) -> None:
        self.requests: list[ResearchRequest] = []

    def run(self, request: ResearchRequest) -> None:
        self.requests.append(request)


def _sample_request() -> ResearchRequest:
    return ResearchRequest(
        input_company="Microsoft",
        as_of_date=date(2026, 7, 31),
        language="zh-CN",
        requested_forms=("10-K", "10-Q"),
    )


def test_pending_to_terminal_runs_flow() -> None:
    """pending 任务：mark_running=True → 加载请求 → 运行 Flow → mark_succeeded。"""
    job_id = uuid.uuid4()
    writer = FakeWriter(running_can_proceed=True)
    flow = FakeFlow()
    service = ExecuteResearchJobService(
        loader=FakeLoader(_sample_request()), writer=writer, flow_runner=flow
    )

    service.process(job_id)

    assert writer.marked_running == [job_id]
    assert writer.marked_succeeded == [job_id]
    assert len(flow.requests) == 1
    assert flow.requests[0].input_company == "Microsoft"


def test_completed_job_not_rerun() -> None:
    """已完成任务（mark_running=False）：不运行 Flow，也不推进状态（防重复执行）。"""
    job_id = uuid.uuid4()
    writer = FakeWriter(running_can_proceed=False)
    flow = FakeFlow()
    service = ExecuteResearchJobService(
        loader=FakeLoader(_sample_request()), writer=writer, flow_runner=flow
    )

    service.process(job_id)

    assert writer.marked_running == [job_id]
    assert writer.marked_succeeded == []
    assert flow.requests == []


def test_missing_job_is_safe_noop() -> None:
    """任务不存在（loader 返回 None）：不运行 Flow，不推进终态。"""
    job_id = uuid.uuid4()
    writer = FakeWriter(running_can_proceed=True)
    flow = FakeFlow()
    service = ExecuteResearchJobService(
        loader=FakeLoader(None), writer=writer, flow_runner=flow
    )

    service.process(job_id)

    assert writer.marked_running == [job_id]
    assert writer.marked_succeeded == []
    assert flow.requests == []
