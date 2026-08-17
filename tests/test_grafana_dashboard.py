"""P05-08 + P06-09C Grafana dashboard 校验测试。

P06-09C 新增断言：
- 7 个 Row 分组（系统健康/HTTP RED/任务与步骤/Agent/PackBoundary/工具与缓存/LLM）；
- datasource uid 全部为 prometheus；timezone browser；refresh 30s；uid/title 保持；
- 每个 Histogram 查询用 ``_bucket`` + ``histogram_quantile``；
- 不含 job_id/company/error_message 高基数 label。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DASHBOARD_PATH = Path("deploy/grafana/provisioning/dashboards/research.json")

# 7 个 Row 分组的固定顺序与标题
EXPECTED_ROWS = [
    "系统健康",
    "HTTP RED",
    "任务与步骤",
    "Agent",
    "PackBoundary",
    "工具与缓存",
    "LLM",
]

# 每个 Histogram 指标都应有一个 P95 面板（查询用 _bucket + histogram_quantile）
HISTOGRAM_METRICS = [
    "http_request_duration_seconds",
    "research_job_duration_seconds",
    "workflow_step_duration_seconds",
    "agent_duration_seconds",
    "tool_duration_seconds",
    "llm_request_duration_seconds",
]

# 应出现在任一查询中的核心 PromQL 指标名
REQUIRED_METRICS = [
    "research_jobs_total",
    "workflow_steps_total",
    "tool_calls_total",
    "tool_retries_total",
    "quality_gate_failures_total",
    "stale_running_steps",
    "stale_recovery_total",
    "failure_total",
    "http_requests_total",
    "http_requests_in_progress",
    "research_jobs_in_progress",
    "agent_runs_total",
    "pack_validation_total",
    "schema_repair_total",
    "analysis_completeness_total",
    "tool_cache_total",
    "llm_requests_total",
    "llm_tokens_total",
    "llm_usage_missing_total",
]


@pytest.fixture()
def dashboard() -> dict:
    data = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    return data


def _all_exprs(dashboard: dict) -> list[str]:
    return [p["targets"][0]["expr"] for p in dashboard["panels"] if p.get("targets")]


def test_dashboard_is_valid_json(dashboard: dict) -> None:
    assert dashboard["title"] == "Invest Research - RED/USE"
    assert dashboard["uid"] == "invest-research-red"
    assert dashboard["timezone"] == "browser"
    assert dashboard["refresh"] == "30s"


def test_dashboard_has_seven_rows_in_order(dashboard: dict) -> None:
    rows = [p for p in dashboard["panels"] if p["type"] == "row"]
    assert [r["title"] for r in rows] == EXPECTED_ROWS


def test_all_panels_use_prometheus_datasource(dashboard: dict) -> None:
    for panel in dashboard["panels"]:
        ds = panel.get("datasource") or {}
        assert ds.get("type") == "prometheus"
        assert ds.get("uid") == "prometheus"


def test_histogram_panels_use_bucket_and_quantile(dashboard: dict) -> None:
    exprs = "\n".join(_all_exprs(dashboard))
    # 每个 Histogram 指标必须出现 _bucket 与 histogram_quantile
    for metric in HISTOGRAM_METRICS:
        assert f"{metric}_bucket" in exprs, f"{metric}_bucket missing"
    assert exprs.count("histogram_quantile(0.95") >= len(HISTOGRAM_METRICS)


def test_required_metrics_present(dashboard: dict) -> None:
    joined = " ".join(_all_exprs(dashboard))
    for metric in REQUIRED_METRICS:
        assert metric in joined, f"missing metric {metric}"


def test_no_high_cardinality_labels(dashboard: dict) -> None:
    joined = " ".join(_all_exprs(dashboard))
    assert "job_id" not in joined
    assert "company" not in joined
    assert "error_message" not in joined


def test_dashboard_has_target_up_and_stale_stats(dashboard: dict) -> None:
    stat_panels = [p for p in dashboard["panels"] if p["type"] == "stat"]
    stat_exprs = [p["targets"][0]["expr"] for p in stat_panels]
    assert len(stat_panels) >= 3
    # 至少一个 Stat 查询 Prometheus target 是否 up（API+Worker）
    assert any("up{" in expr and "invest-research" in expr for expr in stat_exprs)
    # 至少一个 Stat 展示 stale_running_steps
    assert any("stale_running_steps" in expr for expr in stat_exprs)


def test_dashboard_has_refresh_and_timeline(dashboard: dict) -> None:
    assert dashboard["refresh"] == "30s"
    assert dashboard["time"] == {"from": "now-1h", "to": "now"}
