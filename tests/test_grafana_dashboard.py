"""P05-08 Grafana 最小 dashboard 校验测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DASHBOARD_PATH = Path("deploy/grafana/provisioning/dashboards/research.json")


@pytest.fixture()
def dashboard() -> dict:
    data = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    return data


def test_dashboard_is_valid_json(dashboard: dict) -> None:
    assert dashboard["title"] == "Invest Research - RED/USE"
    assert dashboard["uid"] == "invest-research-red"


def test_dashboard_has_required_panels(dashboard: dict) -> None:
    panels = dashboard["panels"]
    exprs = [p["targets"][0]["expr"] for p in panels if p.get("targets")]
    joined = " ".join(exprs)
    # 成功率
    assert "research_jobs_total" in joined
    # P95
    assert "histogram_quantile(0.95" in joined
    # 重试
    assert "tool_retries_total" in joined
    # 错误
    assert "quality_gate_failures_total" in joined


def test_dashboard_has_refresh_and_timeline(dashboard: dict) -> None:
    assert dashboard["refresh"] == "30s"
    assert dashboard["time"] == {"from": "now-1h", "to": "now"}
