"""P06-09C：Prometheus 白名单与 label 约束测试。

验证 deploy/prometheus/prometheus.yml 的 metric_relabel 白名单包含全部
P06-09C 指标及 Histogram 后缀，且指标定义不含高基数 label。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
from invest_research.infrastructure.observability import metrics  # noqa: E402

PROMETHEUS_CONFIG = Path("deploy/prometheus/prometheus.yml")

EXPECTED_METRICS = [
    "research_jobs_total",
    "workflow_steps_total",
    "workflow_step_duration_seconds",
    "tool_calls_total",
    "tool_retries_total",
    "quality_gate_failures_total",
    "stale_running_steps",
    "http_requests_total",
    "http_request_duration_seconds",
    "http_requests_in_progress",
    "research_job_duration_seconds",
    "research_jobs_in_progress",
    "stale_recovery_total",
    "failure_total",
    "agent_runs_total",
    "agent_duration_seconds",
    "pack_validation_total",
    "schema_repair_total",
    "analysis_completeness_total",
    "tool_duration_seconds",
    "tool_cache_total",
    "llm_requests_total",
    "llm_request_duration_seconds",
    "llm_tokens_total",
    "llm_usage_missing_total",
]

HISTOGRAM_METRICS = [
    "workflow_step_duration_seconds",
    "http_request_duration_seconds",
    "research_job_duration_seconds",
    "agent_duration_seconds",
    "tool_duration_seconds",
    "llm_request_duration_seconds",
]

FORBIDDEN_LABELS = ("job_id", "company", "error_message", "url")


@pytest.fixture(scope="module")
def prometheus_cfg() -> str:
    return PROMETHEUS_CONFIG.read_text(encoding="utf-8")


def test_prometheus_config_contains_all_metrics(prometheus_cfg: str) -> None:
    for metric in EXPECTED_METRICS:
        assert metric in prometheus_cfg, f"prometheus.yml 白名单缺少 {metric}"


def test_histogram_metrics_in_whitelist(prometheus_cfg: str) -> None:
    for metric in HISTOGRAM_METRICS:
        assert f"{metric}_bucket" in prometheus_cfg
        assert f"{metric}_count" in prometheus_cfg
        assert f"{metric}_sum" in prometheus_cfg


def test_whitelist_preserves_process_metrics(prometheus_cfg: str) -> None:
    assert "process_.*" in prometheus_cfg


def test_no_metrics_define_high_cardinality_labels() -> None:
    for name in dir(metrics):
        obj = getattr(metrics, name)
        labelnames = getattr(obj, "_labelnames", None)
        if labelnames is None:
            continue
        for label in labelnames:  # type: ignore[union-attr]
            for forbidden in FORBIDDEN_LABELS:
                assert forbidden not in label, f"{name} 含禁用 label {label}"


def test_grafana_dashboard_no_high_cardinality_labels_in_promql() -> None:
    dash = json.loads(
        Path("deploy/grafana/provisioning/dashboards/research.json").read_text(encoding="utf-8")
    )
    exprs = [p["targets"][0]["expr"] for p in dash["panels"] if p.get("targets")]
    joined = " ".join(exprs)
    for forbidden in FORBIDDEN_LABELS:
        assert forbidden not in joined, f"dashboard PromQL 含禁用 label {forbidden}"
