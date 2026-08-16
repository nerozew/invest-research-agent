"""P06-06C：业务指标事件辅助 + Worker 多进程指标服务器单元测试。

覆盖：
- Job 状态计数（research_jobs_total）；
- Step 终态计数与 Histogram observe（workflow_steps_total / duration buckets）；
- Tool 调用与重试（tool_calls_total / tool_retries_total）；
- 质量门禁失败计数（quality_gate_failures_total）；
- 无高基数 label；
- Worker 多进程指标目录清理与信号安装。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

# 必须在 prometheus_client 指标对象导入前确保不使用多进程模式（单进程 REGISTRY）。
os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
from invest_research.infrastructure.observability import metrics  # noqa: E402
from invest_research.infrastructure.observability.metrics_events import (  # noqa: E402
    count_quality_gate_failure,
    count_research_job,
    count_step_terminal,
    count_tool_call,
    count_tool_retry,
    set_stale_running_steps,
)
from invest_research.infrastructure.observability.worker_metrics_server import (  # noqa: E402
    cleanup_multiproc_dir,
    install_worker_signals,
    mark_worker_process_dead,
)


@pytest.mark.parametrize("status", ["pending", "running", "succeeded", "failed", "cancelled"])
def test_research_job_counter_by_status(status: str) -> None:
    before = REGISTRY.get_sample_value("research_jobs_total", {"status": status}) or 0
    count_research_job(status)
    after = REGISTRY.get_sample_value("research_jobs_total", {"status": status}) or 0
    assert after >= before + 1


def test_step_terminal_counter_and_histogram() -> None:
    step = "02_research"
    for status in ("succeeded", "failed_terminal"):
        before_steps = (
            REGISTRY.get_sample_value(
                "workflow_steps_total", {"step": step, "status": status}
            )
            or 0
        )
        count_step_terminal(step, status, duration_seconds=1.5)
        after_steps = (
            REGISTRY.get_sample_value(
                "workflow_steps_total", {"step": step, "status": status}
            )
            or 0
        )
        assert after_steps >= before_steps + 1

    # Histogram：observe 1.5s 落入 [1.0, 2.5) 桶，_count 与 _sum 增加。
    before_count = (
        REGISTRY.get_sample_value(
            "workflow_step_duration_seconds_count", {"step": step}
        )
        or 0
    )
    before_sum = (
        REGISTRY.get_sample_value("workflow_step_duration_seconds_sum", {"step": step})
        or 0
    )
    before_bucket = (
        REGISTRY.get_sample_value(
            "workflow_step_duration_seconds_bucket",
            {"step": step, "le": "2.5"},
        )
        or 0
    )
    count_step_terminal(step, "succeeded", duration_seconds=1.5)
    assert (
        REGISTRY.get_sample_value(
            "workflow_step_duration_seconds_count", {"step": step}
        )
        or 0
    ) >= before_count + 1
    assert (
        REGISTRY.get_sample_value("workflow_step_duration_seconds_sum", {"step": step})
        or 0
    ) >= before_sum + 1.5
    assert (
        REGISTRY.get_sample_value(
            "workflow_step_duration_seconds_bucket",
            {"step": step, "le": "2.5"},
        )
        or 0
    ) >= before_bucket + 1


def test_step_terminal_no_histogram_when_duration_none() -> None:
    step = "07_manifest"
    before_count = (
        REGISTRY.get_sample_value(
            "workflow_step_duration_seconds_count", {"step": step}
        )
        or 0
    )
    count_step_terminal(step, "succeeded", duration_seconds=None)
    # None 时长：只计数步骤，不 observe Histogram。
    after_count = (
        REGISTRY.get_sample_value(
            "workflow_step_duration_seconds_count", {"step": step}
        )
        or 0
    )
    assert after_count == before_count


def test_tool_call_and_retry_counters() -> None:
    tool = "sec_submissions"
    before_calls = (
        REGISTRY.get_sample_value(
            "tool_calls_total", {"tool": tool, "status": "success"}
        )
        or 0
    )
    count_tool_call(tool, "success")
    assert (
        REGISTRY.get_sample_value(
            "tool_calls_total", {"tool": tool, "status": "success"}
        )
        or 0
    ) >= before_calls + 1

    before_retries = (
        REGISTRY.get_sample_value(
            "tool_retries_total", {"tool": tool, "error_code": "RATE_LIMITED"}
        )
        or 0
    )
    count_tool_retry(tool, "RATE_LIMITED")
    assert (
        REGISTRY.get_sample_value(
            "tool_retries_total", {"tool": tool, "error_code": "RATE_LIMITED"}
        )
        or 0
    ) >= before_retries + 1


def test_quality_gate_failure_counter() -> None:
    gate = "missing_section"
    before = (
        REGISTRY.get_sample_value(
            "quality_gate_failures_total", {"gate": gate}
        )
        or 0
    )
    count_quality_gate_failure(gate)
    after = (
        REGISTRY.get_sample_value(
            "quality_gate_failures_total", {"gate": gate}
        )
        or 0
    )
    assert after >= before + 1


def test_stale_running_steps_gauge() -> None:
    set_stale_running_steps(3)
    value = REGISTRY.get_sample_value("stale_running_steps") or 0
    assert value == 3


def test_no_high_cardinality_labels() -> None:
    """所有业务指标 label 不得包含 job_id/url/company 高基数字段。"""
    for metric in (
        metrics.research_jobs_total,
        metrics.workflow_steps_total,
        metrics.workflow_step_duration_seconds,
        metrics.tool_calls_total,
        metrics.tool_retries_total,
        metrics.quality_gate_failures_total,
    ):
        for label in metric._labelnames:  # noqa: SLF001 - 测试访问内部 label 列表
            assert "job_id" not in label
            assert "url" not in label
            assert "company" not in label


def test_cleanup_multiproc_dir_removes_db_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    (tmp_path / "pid_1234.db").write_bytes(b"fake")
    (tmp_path / "pid_5678.db").write_bytes(b"fake")
    cleanup_multiproc_dir()
    assert list(tmp_path.glob("*.db")) == []


def test_cleanup_multiproc_dir_creates_missing_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "nested" / "metrics"
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(target))
    cleanup_multiproc_dir()
    assert target.is_dir()


def test_mark_worker_process_dead_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """mark_process_dead 对不存在的 pid 不应抛异常（尽力而为清理）。"""
    import prometheus_client.multiprocess as mp

    def _fake_mark_dead(pid: int, path: str) -> None:
        assert pid == 999999
        assert path.endswith("prometheus_metrics")

    monkeypatch.setattr(mp, "mark_process_dead", _fake_mark_dead)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_metrics")
    mark_worker_process_dead(999999)  # 不应抛异常


def test_install_worker_signals_registers_handlers() -> None:
    """install_worker_signals 注册 Celery 信号后不抛异常（弱隔离断言）。"""
    from celery import Celery

    app = Celery("test-metrics-app", broker="memory://")
    install_worker_signals(app)
    # 信号已通过 weak=False 注册；再次调用不重复导致异常。
    install_worker_signals(app)
