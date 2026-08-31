"""P06-06A/B 前端测试：档位徽章、当前阶段中文映射、旧任务兼容。

- fast/deep 请求字段正确传递；
- job_list_row / render_job_snapshot 显示档位；
- 旧响应缺字段时按 deep 兼容（profile_badge）；
- 当前阶段中文映射（current_stage_label）；
- 步骤状态图标；
- 旧任务 steps=[] 兼容文案（render_job_snapshot 不抛错）。
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

import httpx

from invest_research.domain.models import ResearchRequest
from invest_research.domain.status import JobStatus, StepStatus
from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.models import JobListEntry, JobSnapshot, StepSnapshot
from invest_research.frontend.render import (
    CURRENT_STAGE_LABELS,
    current_stage_label,
    job_list_row,
    profile_badge,
)

API_BASE = "http://api.test"


def _make_client(handler: httpx.Request) -> ResearchApiClient:
    transport = httpx.MockTransport(handler)
    return ResearchApiClient(base_url=API_BASE, timeout=5.0, transport=transport)


# ---------------------------------------------------------------------------
# P06-06A：fast/deep 请求字段
# ---------------------------------------------------------------------------


def test_create_request_fast_explicit() -> None:
    """fast 档位显式传给请求体（不依赖领域默认）。"""
    request = ResearchRequest(
        input_company="AAPL",
        as_of_date=date(2025, 10, 31),
        language="zh-CN",
        requested_forms=("10-K",),
        research_profile="fast",
    )
    assert request.research_profile == "fast"
    assert request.model_dump()["research_profile"] == "fast"


def test_create_request_deep_explicit() -> None:
    request = ResearchRequest(
        input_company="AAPL",
        as_of_date=date(2025, 10, 31),
        language="zh-CN",
        requested_forms=("10-K",),
        research_profile="deep",
    )
    assert request.research_profile == "deep"


def test_payload_passes_research_profile_to_api() -> None:
    """client.create_research_job 发送的 JSON 含 research_profile 字段。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            202,
            json={
                "job_id": str(uuid.uuid4()),
                "status": "pending",
            },
        )

    client = _make_client(handler)
    request = ResearchRequest(
        input_company="AAPL",
        as_of_date=date(2025, 10, 31),
        language="zh-CN",
        requested_forms=("10-K",),
        research_profile="fast",
    )
    client.create_research_job(request=request, idempotency_key="k-1")
    assert captured["json"]["research_profile"] == "fast"


# ---------------------------------------------------------------------------
# P06-06A：档位徽章
# ---------------------------------------------------------------------------


def test_profile_badge_fast() -> None:
    assert profile_badge("fast") == "⚡ 快速"


def test_profile_badge_deep() -> None:
    assert profile_badge("deep") == "🔬 深度"


def test_profile_badge_missing_falls_back_deep() -> None:
    """旧响应缺 research_profile 字段时按 deep 兼容（不抛错）。"""
    assert profile_badge(None) == "🔬 深度"
    assert profile_badge("") == "🔬 深度"


def test_profile_badge_unknown_falls_back_raw() -> None:
    assert profile_badge("ultra") == "ultra"


def _list_entry(*, research_profile: str = "deep") -> JobListEntry:
    return JobListEntry(
        job_id=uuid.uuid4(),
        input_company="AAPL",
        as_of_date=date(2025, 10, 31),
        language="zh-CN",
        status=JobStatus.RUNNING,
        current_step="02_research",
        research_profile=research_profile,
        created_at=datetime(2025, 11, 1, tzinfo=timezone.utc),
    )


def test_job_list_row_shows_profile_badge() -> None:
    row = job_list_row(_list_entry(research_profile="fast"))
    assert row["档位"] == "⚡ 快速"
    assert row["状态"] == "🔄 执行中"
    assert row["当前阶段"] == "正在搜索 SEC 与公开资料"


def test_job_list_row_profile_deep_badge() -> None:
    row = job_list_row(_list_entry(research_profile="deep"))
    assert row["档位"] == "🔬 深度"


# ---------------------------------------------------------------------------
# P06-06B：当前阶段中文映射
# ---------------------------------------------------------------------------


def test_current_stage_label_mapping() -> None:
    expected = {
        "00_request": "正在初始化任务",
        "01_company_resolve": "正在解析公司",
        "02_research": "正在搜索 SEC 与公开资料",
        "03_documents": "正在下载和解析财报",
        "04_analysis": "正在分析财务数据",
        "05_writer": "正在撰写报告",
        "06_quality_gate": "正在检查报告质量",
        "07_manifest": "正在生成最终工件",
    }
    assert CURRENT_STAGE_LABELS == expected
    for key, label in expected.items():
        assert current_stage_label(key) == label


def test_current_stage_label_none() -> None:
    assert current_stage_label(None) is None
    assert current_stage_label("") is None


def test_current_stage_label_unknown_passthrough() -> None:
    assert current_stage_label("99_unknown") == "99_unknown"


# ---------------------------------------------------------------------------
# P06-06B：详情快照 steps=[] 兼容 + 步骤状态图标
# ---------------------------------------------------------------------------


def _snapshot(steps: tuple[StepSnapshot, ...]) -> JobSnapshot:
    return JobSnapshot(
        job_id=uuid.uuid4(),
        status=JobStatus.RUNNING,
        current_step="02_research",
        research_profile="fast",
        steps=steps,
    )


class _FakeColumn:
    """支持 with 上下文管理器的 fake col（模拟 st.columns 返回的列对象）。"""

    def __enter__(self) -> "_FakeColumn":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakeColumns:
    """st.columns(n) 返回的 fake 列容器（每个元素支持 with 协议）。"""

    def __init__(self, n: int) -> None:
        self._cols = [_FakeColumn() for _ in range(n)]

    def __getitem__(self, idx: int) -> _FakeColumn:
        return self._cols[idx]


def test_render_job_snapshot_steps_empty_does_not_raise(
    monkeypatch,
) -> None:
    """旧任务 steps=[]：render_job_snapshot 不抛错，走兼容分支。"""
    import streamlit as st

    calls: list[str] = []

    def fake_info(msg: str) -> None:
        calls.append(str(msg))

    monkeypatch.setattr(st, "info", fake_info)
    monkeypatch.setattr(st, "subheader", lambda *a, **k: None)
    monkeypatch.setattr(st, "columns", lambda n: _FakeColumns(n))
    monkeypatch.setattr(st, "metric", lambda *a, **k: None)
    monkeypatch.setattr(st, "markdown", lambda *a, **k: None)

    from invest_research.frontend.render import render_job_snapshot

    render_job_snapshot(_snapshot(()))
    assert any("旧版执行记录" in c for c in calls)


def test_render_job_snapshot_shows_step_icons(monkeypatch) -> None:
    """详情步骤表显示状态图标（✅ 完成 / 🔄 进行中）。"""
    import streamlit as st

    captured_table: list = []

    monkeypatch.setattr(st, "subheader", lambda *a, **k: None)
    monkeypatch.setattr(st, "columns", lambda n: _FakeColumns(n))
    monkeypatch.setattr(st, "metric", lambda *a, **k: None)
    monkeypatch.setattr(st, "info", lambda *a, **k: None)
    monkeypatch.setattr(st, "table", lambda rows: captured_table.append(rows))

    from invest_research.frontend.render import render_job_snapshot

    steps = (
        StepSnapshot(
            step_name="01_company_resolve",
            sequence_no=1,
            status=StepStatus.SUCCEEDED,
            attempt_count=1,
        ),
        StepSnapshot(
            step_name="02_research",
            sequence_no=2,
            status=StepStatus.RUNNING,
            attempt_count=1,
        ),
    )
    render_job_snapshot(_snapshot(steps))
    assert len(captured_table) == 1
    statuses = [row["状态"] for row in captured_table[0]]
    assert "✅ 完成" in statuses
    assert "🔄 进行中" in statuses


# ---------------------------------------------------------------------------
# P06-06B：GET job running 期间返回 current_step 和 steps（client 契约）
# ---------------------------------------------------------------------------


def test_get_job_running_parses_current_step_and_steps() -> None:
    """running 期间 API 返回 current_step + steps（frontend client 契约验证）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "job_id": str(uuid.uuid4()),
                "status": "running",
                "current_step": "04_analysis",
                "research_profile": "fast",
                "steps": [
                    {
                        "step_name": "02_research",
                        "sequence_no": 2,
                        "status": "succeeded",
                        "attempt_count": 1,
                        "error_code": None,
                        "error_message": None,
                        "duration_seconds": 1.5,
                    },
                    {
                        "step_name": "04_analysis",
                        "sequence_no": 4,
                        "status": "running",
                        "attempt_count": 1,
                        "error_code": None,
                        "error_message": None,
                        "duration_seconds": None,
                    },
                ],
            },
        )

    client = _make_client(handler)
    snapshot = client.get_research_job(str(uuid.uuid4()))

    assert snapshot.current_step == "04_analysis"
    assert snapshot.research_profile == "fast"
    assert len(snapshot.steps) == 2
    assert snapshot.steps[0].status == StepStatus.SUCCEEDED
    assert snapshot.steps[1].status == StepStatus.RUNNING
    assert current_stage_label(snapshot.current_step) == "正在分析财务数据"
