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
    count_agent_run,
    count_analysis_completeness,
    count_failure,
    count_http_requests,
    count_llm_request,
    count_llm_tokens,
    count_llm_usage_missing,
    count_pack_validation,
    count_quality_gate_failure,
    count_research_job,
    count_schema_repair,
    count_step_terminal,
    count_tool_cache,
    count_tool_call,
    count_tool_retry,
    label_provider_model,
    observe_agent_duration,
    observe_http_request,
    observe_llm_duration,
    observe_research_job,
    observe_tool_duration,
    set_http_in_progress,
    set_research_job_in_progress,
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


# ---------------------------------------------------------------------------
# P06-09C：HTTP RED / Job 转换 / Agent Histogram / Pack / 工具缓存 / LLM usage
# ---------------------------------------------------------------------------


def test_http_red_metrics() -> None:
    """HTTP RED：请求计数按状态分类、耗时 observe、进行中 Gauge 增减。"""
    count_http_requests("POST", "/v1/research-jobs", 202)
    count_http_requests("GET", "/health", 500)
    assert (REGISTRY.get_sample_value(
        "http_requests_total", {"method": "POST", "route": "/v1/research-jobs",
                                "status_class": "2xx"}) or 0) >= 1
    # 500 → 5xx 分类
    assert (REGISTRY.get_sample_value(
        "http_requests_total", {"method": "GET", "route": "/health",
                                "status_class": "5xx"}) or 0) >= 1

    before_count = REGISTRY.get_sample_value(
        "http_request_duration_seconds_count",
        {"method": "GET", "route": "/health"}) or 0
    before_sum = REGISTRY.get_sample_value(
        "http_request_duration_seconds_sum",
        {"method": "GET", "route": "/health"}) or 0
    observe_http_request("GET", "/health", 0.05)
    assert (REGISTRY.get_sample_value(
        "http_request_duration_seconds_count",
        {"method": "GET", "route": "/health"}) or 0) >= before_count + 1
    assert (REGISTRY.get_sample_value(
        "http_request_duration_seconds_sum",
        {"method": "GET", "route": "/health"}) or 0) >= before_sum + 0.05

    set_http_in_progress("GET", "/health", 1)
    set_http_in_progress("GET", "/health", -1)
    # 净增 0：Gauge 回到原值（不影响业务）
    assert REGISTRY.get_sample_value(
        "http_requests_in_progress", {"method": "GET", "route": "/health"}) in (None, 0)


def test_research_job_conversion_metrics() -> None:
    """Job 终态转换：observe 耗时 + 执行中 Gauge + 失败分类。"""
    observe_research_job("fast", "succeeded", 3.5)
    before_count = REGISTRY.get_sample_value(
        "research_job_duration_seconds_count",
        {"profile": "fast", "status": "succeeded"}) or 0
    before_sum = REGISTRY.get_sample_value(
        "research_job_duration_seconds_sum",
        {"profile": "fast", "status": "succeeded"}) or 0
    observe_research_job("fast", "succeeded", 3.5)
    assert (REGISTRY.get_sample_value(
        "research_job_duration_seconds_count",
        {"profile": "fast", "status": "succeeded"}) or 0) >= before_count + 1
    assert (REGISTRY.get_sample_value(
        "research_job_duration_seconds_sum",
        {"profile": "fast", "status": "succeeded"}) or 0) >= before_sum + 3.5

    set_research_job_in_progress("deep", 1)
    set_research_job_in_progress("deep", -1)
    assert REGISTRY.get_sample_value(
        "research_jobs_in_progress", {"profile": "deep"}) in (None, 0)

    count_failure("04_analysis", "SCHEMA_INVALID")
    assert (REGISTRY.get_sample_value(
        "failure_total",
        {"stage": "04_analysis", "error_code": "SCHEMA_INVALID"}) or 0) >= 1


def test_agent_run_and_duration_histogram() -> None:
    """Agent 完成计数（role 白名单）+ 耗时 Histogram。"""
    count_agent_run("research", "fast", "qwen", "qwen-max", "success")
    before_count = REGISTRY.get_sample_value(
        "agent_duration_seconds_count",
        {"role": "research", "profile": "fast", "provider": "qwen",
         "model": "qwen-max", "status": "success"}) or 0
    before_bucket = REGISTRY.get_sample_value(
        "agent_duration_seconds_bucket",
        {"role": "research", "profile": "fast", "provider": "qwen",
         "model": "qwen-max", "status": "success", "le": "1.0"}) or 0
    observe_agent_duration("research", "fast", "qwen", "qwen-max", "success", 0.8)
    assert (REGISTRY.get_sample_value(
        "agent_duration_seconds_count",
        {"role": "research", "profile": "fast", "provider": "qwen",
         "model": "qwen-max", "status": "success"}) or 0) >= before_count + 1
    assert (REGISTRY.get_sample_value(
        "agent_duration_seconds_bucket",
        {"role": "research", "profile": "fast", "provider": "qwen",
         "model": "qwen-max", "status": "success", "le": "1.0"}) or 0) >= before_bucket + 1


def test_agent_role_whitelist_rejected() -> None:
    """非白名单 role 不写指标（避免意外高基数）。"""
    before = REGISTRY.get_sample_value(
        "agent_runs_total",
        {"role": "unknown_role", "profile": "fast", "provider": "qwen",
         "model": "m", "status": "success"}) or 0
    count_agent_run("unknown_role", "fast", "qwen", "m", "success")
    after = REGISTRY.get_sample_value(
        "agent_runs_total",
        {"role": "unknown_role", "profile": "fast", "provider": "qwen",
         "model": "m", "status": "success"}) or 0
    assert after == before


def test_pack_validation_and_repair_and_completeness() -> None:
    """PackBoundary 校验结果 / schema 修复 / completeness。"""
    count_pack_validation("04_analysis", "FinancialAnalysisPack", "success", "NONE")
    count_pack_validation("04_analysis", "FinancialAnalysisPack", "failed", "SCHEMA_INVALID")
    assert (REGISTRY.get_sample_value(
        "pack_validation_total",
        {"stage": "04_analysis", "pack_type": "FinancialAnalysisPack",
         "result": "success", "error_code": "NONE"}) or 0) >= 1
    assert (REGISTRY.get_sample_value(
        "pack_validation_total",
        {"stage": "04_analysis", "pack_type": "FinancialAnalysisPack",
         "result": "failed", "error_code": "SCHEMA_INVALID"}) or 0) >= 1

    count_schema_repair("04_analysis", "FinancialAnalysisPack", "repaired")
    assert (REGISTRY.get_sample_value(
        "schema_repair_total",
        {"stage": "04_analysis", "pack_type": "FinancialAnalysisPack",
         "result": "repaired"}) or 0) >= 1

    count_analysis_completeness("partial")
    assert (REGISTRY.get_sample_value(
        "analysis_completeness_total", {"status": "partial"}) or 0) >= 1


def test_tool_cache_and_duration() -> None:
    """工具缓存 hit/miss + 工具耗时 Histogram。"""
    count_tool_cache("sec_submissions", "hit")
    assert (REGISTRY.get_sample_value(
        "tool_cache_total", {"tool": "sec_submissions", "result": "hit"}) or 0) >= 1

    before_bucket = REGISTRY.get_sample_value(
        "tool_duration_seconds_bucket",
        {"tool": "sec_submissions", "status": "success", "le": "0.1"}) or 0
    observe_tool_duration("sec_submissions", "success", 0.05)
    assert (REGISTRY.get_sample_value(
        "tool_duration_seconds_bucket",
        {"tool": "sec_submissions", "status": "success", "le": "0.1"}) or 0) >= before_bucket + 1


def test_llm_usage_present_and_missing() -> None:
    """LLM：usage 存在时按类型计数；缺失时记录 usage_missing 且不伪造 0。"""
    before_input = REGISTRY.get_sample_value(
        "llm_tokens_total",
        {"provider": "qwen", "model": "qwen-max", "role": "research", "type": "input"}) or 0
    count_llm_tokens("qwen", "qwen-max", "research", "input", 120)
    assert (REGISTRY.get_sample_value(
        "llm_tokens_total",
        {"provider": "qwen", "model": "qwen-max", "role": "research", "type": "input"}) or 0) \
        >= before_input + 120

    before_missing = REGISTRY.get_sample_value(
        "llm_usage_missing_total",
        {"provider": "qwen", "model": "qwen-max", "role": "writer"}) or 0
    count_llm_usage_missing("qwen", "qwen-max", "writer")
    assert (REGISTRY.get_sample_value(
        "llm_usage_missing_total",
        {"provider": "qwen", "model": "qwen-max", "role": "writer"}) or 0) >= before_missing + 1

    # usage 缺失时不写 tokens（type 白名单非法值被拒绝）
    before_bad = REGISTRY.get_sample_value(
        "llm_tokens_total",
        {"provider": "qwen", "model": "qwen-max", "role": "writer", "type": "cached_input"}) or 0
    count_llm_tokens("qwen", "qwen-max", "writer", "cached_input", -5)
    assert (REGISTRY.get_sample_value(
        "llm_tokens_total",
        {"provider": "qwen", "model": "qwen-max", "role": "writer", "type": "cached_input"}) or 0) \
        == before_bad

    # LLM 请求计数与耗时
    count_llm_request("qwen", "qwen-max", "research", "success")
    llm_req_value = REGISTRY.get_sample_value(
        "llm_requests_total",
        {"provider": "qwen", "model": "qwen-max", "role": "research", "status": "success"},
    ) or 0
    assert llm_req_value >= 1
    before_llm_dur = REGISTRY.get_sample_value(
        "llm_request_duration_seconds_count",
        {"provider": "qwen", "model": "qwen-max", "role": "research", "status": "success"}) or 0
    observe_llm_duration("qwen", "qwen-max", "research", "success", 2.0)
    assert (REGISTRY.get_sample_value(
        "llm_request_duration_seconds_count",
        {"provider": "qwen", "model": "qwen-max", "role": "research", "status": "success"}) or 0) \
        >= before_llm_dur + 1


def test_label_provider_model_prefers_explicit_vendor() -> None:
    """P06-11：优先使用显式 vendor（qwen/deepseek/generic），不再依赖 base_url 猜测。"""
    assert label_provider_model("qwen", "Qwen-Max") == ("qwen", "qwen-max")
    assert label_provider_model("deepseek", "deepseek-v4-flash") == (
        "deepseek", "deepseek-v4-flash")
    # generic 保留为 label（不尝试回退 base_url）
    assert label_provider_model("generic", "gpt-4o") == ("generic", "gpt-4o")


def test_label_provider_model_falls_back_to_base_url() -> None:
    """vendor 缺失/未知时回退 base_url 识别（dashscope/deepseek/其它）。"""
    assert label_provider_model(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="Qwen-Max",
    ) == ("qwen", "qwen-max")
    assert label_provider_model(
        vendor="",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-flash",
    ) == ("deepseek", "deepseek-v4-flash")
    assert label_provider_model(
        vendor="unknown-vendor",
        base_url="https://unknown.example/v1",
        model="gpt-4o",
    ) == ("openai_compatible", "gpt-4o")
    # vendor 为空但 base_url 含 openai → openai
    assert label_provider_model(
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
    ) == ("openai", "gpt-4o")


def test_no_high_cardinality_labels_p06_09c() -> None:
    """P06-09C 新增指标同样不得含 job_id/company/error_message 高基数 label。"""
    p06_09c_metrics = (
        metrics.http_requests_total,
        metrics.http_request_duration_seconds,
        metrics.http_requests_in_progress,
        metrics.research_job_duration_seconds,
        metrics.research_jobs_in_progress,
        metrics.stale_recovery_total,
        metrics.failure_total,
        metrics.agent_runs_total,
        metrics.agent_duration_seconds,
        metrics.pack_validation_total,
        metrics.schema_repair_total,
        metrics.analysis_completeness_total,
        metrics.tool_duration_seconds,
        metrics.tool_cache_total,
        metrics.llm_requests_total,
        metrics.llm_request_duration_seconds,
        metrics.llm_tokens_total,
        metrics.llm_usage_missing_total,
    )
    for metric in p06_09c_metrics:
        for label in metric._labelnames:  # noqa: SLF001 - 测试访问内部 label 列表
            assert "job_id" not in label, f"{metric._name} label {label} job_id"
            assert "company" not in label, f"{metric._name} label {label} company"
            assert "error_message" not in label, f"{metric._name} label {label} error_message"
            assert "url" not in label, f"{metric._name} label {label} url"
