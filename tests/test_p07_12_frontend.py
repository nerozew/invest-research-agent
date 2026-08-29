"""P07-12：年度模式前端契约与脱敏展示测试。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from invest_research.domain.annual_node_runtime import (
    AnnualNodeEventSnapshot,
    AnnualNodeEventType,
    AnnualNodeGraphSnapshot,
    AnnualNodeWait,
)
from invest_research.domain.annual_pipeline import (
    ResearchMode,
    ResearchNode,
    ResearchNodeKind,
    ResearchNodeStatus,
)
from invest_research.domain.status import JobStatus
from invest_research.frontend.models import JobSnapshot
from invest_research.frontend.render import (
    annual_event_rows,
    annual_node_rows,
    annual_wait_rows,
    artifact_category,
    is_viewable_json_artifact,
    render_job_snapshot,
    research_mode_badge,
)
from invest_research.frontend.research_mode import request_options_for_mode


def _graph() -> AnnualNodeGraphSnapshot:
    return AnnualNodeGraphSnapshot(
        job_id=str(uuid.uuid4()),
        nodes=(
            ResearchNode(
                node_key="annual_selection",
                kind=ResearchNodeKind.DISCOVER_ANNUAL_FILINGS,
                status=ResearchNodeStatus.SUCCEEDED,
                attempt_count=1,
                output_artifact_keys=("annual/runtime_state.json",),
                input_fingerprint="must-not-be-rendered",
            ),
            ResearchNode(
                node_key="annual_evidence_fanout",
                kind=ResearchNodeKind.VALIDATE_EVIDENCE,
                status=ResearchNodeStatus.PENDING,
            ),
        ),
        waiting=(
            AnnualNodeWait(
                node_key="annual_evidence_fanout",
                upstream_node_key="annual_selection",
                upstream_status=ResearchNodeStatus.SUCCEEDED,
                reason_code="upstream_pending",
            ),
        ),
        recent_events=(
            AnnualNodeEventSnapshot(
                node_key="annual_selection",
                event_no=1,
                event_type=AnnualNodeEventType.STATE_CHANGED,
                previous_status=ResearchNodeStatus.RUNNING,
                new_status=ResearchNodeStatus.SUCCEEDED,
                reason_code=None,
                artifact_keys=("annual/runtime_state.json",),
                attempt_count=1,
                created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
            ),
        ),
        budget_stop_reasons=("no_evidence_gain",),
        critical_path_node_keys=("annual_selection", "annual_evidence_fanout"),
        critical_path_seconds=12.5,
    )


def test_annual_request_options_are_locked_to_deep_and_10k() -> None:
    forms, profile = request_options_for_mode(
        ResearchMode.ANNUAL_DEEP,
        requested_forms=("10-Q", "10-K/A"),
        research_profile="fast",
    )
    assert forms == ("10-K",)
    assert profile == "deep"


def test_legacy_request_options_preserve_user_choice() -> None:
    forms, profile = request_options_for_mode(
        ResearchMode.LEGACY,
        requested_forms=("10-K", "10-Q"),
        research_profile="fast",
    )
    assert forms == ("10-K", "10-Q")
    assert profile == "fast"


def test_frontend_snapshot_parses_annual_mode_and_graph() -> None:
    graph = _graph()
    snapshot = JobSnapshot.model_validate(
        {
            "job_id": str(uuid.uuid4()),
            "status": "running",
            "research_mode": "annual_deep",
            "annual_nodes": graph.model_dump(mode="json"),
        }
    )
    assert snapshot.research_mode is ResearchMode.ANNUAL_DEEP
    assert snapshot.annual_nodes is not None
    assert snapshot.annual_nodes.nodes[0].node_key == "annual_selection"


def test_frontend_snapshot_missing_mode_keeps_legacy_compatibility() -> None:
    snapshot = JobSnapshot.model_validate({"job_id": str(uuid.uuid4()), "status": "pending"})
    assert snapshot.research_mode is ResearchMode.LEGACY
    assert snapshot.annual_nodes is None


def test_annual_rows_only_contain_snapshot_whitelist() -> None:
    graph = _graph()
    node = annual_node_rows(graph)[0]
    assert node["节点"] == "选择目标/上年 10-K"
    assert "input_fingerprint" not in node
    assert "must-not-be-rendered" not in str(node)
    wait = annual_wait_rows(graph)[0]
    assert set(wait) == {"等待节点", "未完成上游", "上游状态", "原因"}
    event = annual_event_rows(graph)[0]
    assert set(event) == {"时间", "节点", "事件", "状态", "原因", "尝试", "工件"}


def test_annual_render_uses_nodes_not_legacy_empty_steps_notice(monkeypatch) -> None:
    import streamlit as st

    messages: list[str] = []
    tables: list[object] = []

    class _Column:
        def __enter__(self) -> "_Column":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "columns", lambda count: [_Column() for _ in range(count)])
    monkeypatch.setattr(st, "metric", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        st, "warning", lambda message, *args, **kwargs: messages.append(str(message))
    )
    monkeypatch.setattr(st, "info", lambda message, *args, **kwargs: messages.append(str(message)))
    monkeypatch.setattr(st, "table", lambda rows: tables.append(rows))

    render_job_snapshot(
        JobSnapshot(
            job_id=uuid.uuid4(),
            status=JobStatus.RUNNING,
            research_mode=ResearchMode.ANNUAL_DEEP,
            annual_nodes=_graph(),
        )
    )
    assert tables
    assert not any("旧版执行记录" in message for message in messages)


def test_annual_artifact_categories_and_raw_sec_autoload_protection() -> None:
    assert artifact_category("annual/comparison.json", "pack") == "财务比较"
    assert artifact_category("annual/sections/risk_factors.json", "pack") == "章节产物"
    assert artifact_category("annual/runtime_state.json", "pack") == "年度运行状态"
    assert artifact_category("other.json", "pack") == "通用工件"
    assert is_viewable_json_artifact("annual/0001/source.html") is False
    assert is_viewable_json_artifact("annual/0001/parsed.json") is False
    assert is_viewable_json_artifact("annual/comparison.json") is True
    assert research_mode_badge(ResearchMode.ANNUAL_DEEP) == "📅 年度深度研究"
