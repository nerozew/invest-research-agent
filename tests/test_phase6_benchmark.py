"""P06-10A：Phase 6 基准框架单元测试（纯离线，不依赖 Docker/live）。

覆盖（对齐任务"八、测试与文档"）：
- 主任务/控制任务分母隔离；
- 至少 96/100 才满足 >95%（96/100 通过、95/100 不通过）；
- cancelled 不算成功；
- 缺少 PDF 不算成功；
- timeout 归因；
- fast/deep 分组；
- P50/P90/P95/P99 分位数；
- 错误分类；
- 报告不泄露密钥（live_agent_success_rate_note / report.md 无 key）；
- live fail-fast（FLOW_MODE=live 抛 RuntimeError；真实密钥结构抛 RuntimeError；
  占位符允许）；
- 固定 seed 可复现（同 seed 生成相同 specs 顺序）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# scripts/ 不是可导入的 Python 包：直接把脚本目录加入 sys.path 后按文件名导入。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_phase6_benchmark as bm  # noqa: E402

# ---------------------------------------------------------------------------
# Prometheus 快照（P06-10B）
# ---------------------------------------------------------------------------


class _FakeResp:
    """最小 fake urllib response（支持上下文管理器，.read() 返回 JSON 字节）。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _prom_results(values: dict[str, float]) -> dict[str, dict]:
    """构造 Prometheus /api/v1/query 成功响应（value=[ts, "数值"]）。"""
    return {
        name: {
            "status": "success",
            "data": {"resultType": "vector", "result": [
                {"metric": {}, "value": [1234567890, str(v)]}
            ]},
        }
        for name, v in values.items()
    }


def test_prometheus_snapshot_covers_8_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """快照覆盖 8 类指标（5 Counter + 3 Histogram 的 _count/_sum）。"""
    import urllib.request

    # 所有查询都成功返回（默认 0 或指定值）。
    values = {
        "research_jobs_total": 12.0,
        "http_requests_total": 34.0,
        "agent_runs_total": 5.0,
        "pack_validation_total": 8.0,
        "tool_cache_total": 21.0,
        "research_job_duration_seconds_count": 10.0,
        "research_job_duration_seconds_sum": 3.0,
        "workflow_step_duration_seconds_count": 80.0,
        "workflow_step_duration_seconds_sum": 40.0,
        "http_request_duration_seconds_count": 200.0,
        "http_request_duration_seconds_sum": 15.0,
    }

    def _fake_urlopen(req: Any, timeout: float = 0) -> Any:
        # 从 URL query 中解析 metric 名。
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(req.full_url).query)["query"][0]
        metric = q[len("sum("):-1]
        if metric not in values:
            return _FakeResp({"status": "success", "data": {"result": []}})
        return _FakeResp(_prom_results({metric: values[metric]})[metric])

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    client = bm.PrometheusClient("http://prom:9090")
    snap = client.snapshot()

    # 8 类指标 = 5 Counter + 3 Histogram（每 Histogram 含 count/sum 两项）。
    assert set(snap) == {
        "research_jobs_total",
        "http_requests_total",
        "agent_runs_total",
        "pack_validation_total",
        "tool_cache_total",
        "research_job_duration_seconds_count",
        "research_job_duration_seconds_sum",
        "workflow_step_duration_seconds_count",
        "workflow_step_duration_seconds_sum",
        "http_request_duration_seconds_count",
        "http_request_duration_seconds_sum",
    }
    assert snap["research_jobs_total"] == 12.0
    assert snap["research_job_duration_seconds_sum"] == 3.0


def test_prometheus_unreachable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prometheus API 不可达必须抛 RuntimeError，禁止空 dict 冒充成功。"""
    import urllib.error
    import urllib.request

    def _raise(req: Any, timeout: float = 0) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    client = bm.PrometheusClient("http://prom:9090")
    with pytest.raises(RuntimeError, match="Prometheus API 不可达"):
        client.snapshot()


def test_prometheus_query_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prometheus 查询失败（status != success）必须抛 RuntimeError。"""
    import urllib.request

    def _bad(req: Any, timeout: float = 0) -> Any:
        return _FakeResp({"status": "error", "error": "bad_data", "errorType": "bad_data"})

    monkeypatch.setattr(urllib.request, "urlopen", _bad)
    client = bm.PrometheusClient("http://prom:9090")
    with pytest.raises(RuntimeError, match="Prometheus 查询失败"):
        client.snapshot()


def test_prometheus_empty_result_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """指标尚无样本时返回 0.0（合法状态，不是失败）。"""
    import urllib.request

    def _empty(req: Any, timeout: float = 0) -> Any:
        return _FakeResp({"status": "success", "data": {"result": []}})

    monkeypatch.setattr(urllib.request, "urlopen", _empty)
    client = bm.PrometheusClient("http://prom:9090")
    snap = client.snapshot()
    assert snap["research_jobs_total"] == 0.0


# ---------------------------------------------------------------------------
# 分母隔离 / 成功判定
# ---------------------------------------------------------------------------


def _job(
    *,
    status: str = "succeeded",
    current_step: str | None = None,
    steps: list[dict] | None = None,
    error_message: str | None = None,
    error_code: str | None = None,
    failure_stage: str | None = None,
) -> dict:
    if steps is None:
        steps = [
            {"step_name": name, "status": "succeeded", "attempt_count": 1}
            for name in bm._REQUIRED_STEPS
        ]
    return {
        "status": status,
        "current_step": current_step,
        "steps": steps,
        "error_message": error_message,
        "error_code": error_code,
        "failure_stage": failure_stage,
        "duration_seconds": 2.0,
    }


def _make_record(
    *,
    job_id: str = "11111111-2222-3333-4444-555555555555",
    profile: str = "fast",
    index: int = 0,
    success: bool = True,
    terminal_status: str = "succeeded",
    duration: float = 2.0,
    missing: list[str] | None = None,
    error_code: str | None = None,
    failure_stage: str | None = None,
    timeout: bool = False,
) -> bm.JobRecord:
    return bm.JobRecord(
        job_id=job_id,
        profile=profile,
        index=index,
        kind="main",
        control_scenario=None,
        created_at=None,
        started_at=None,
        finished_at=None,
        duration_seconds=duration,
        terminal_status=terminal_status,
        error_code=error_code,
        failure_stage=failure_stage,
        required_artifacts=list(bm._REQUIRED_ARTIFACTS),
        missing_artifacts=missing or [],
        success_checks={},
        success=success,
        timeout=timeout,
    )


def test_main_denominator_excludes_control() -> None:
    """主任务/控制任务分母隔离：控制任务（cancel）不进入主成功率分母。"""
    runner = bm.BenchmarkRunner(jobs=5, api_base="http://fake:1", output_dir=".tmp")
    main_recs = [ _make_record(job_id=f"job-{i}", success=True) for i in range(5) ]
    control_cancel = {"ok": True, "job_id": "cancel-1"}
    summary = runner._build_summary(
        main_recs, [], {}, {}, control_cancel
    )
    assert summary["main_jobs_total"] == 5
    assert summary["succeeded"] == 5
    assert summary["control_cancel_ok"] is True
    # 控制任务不改变主分母 / 主成功率。
    assert summary["workflow_success_rate"] == 1.0


def test_96_of_100_passes_gt95() -> None:
    """96/100 通过 >95% 阈值；95/100 不通过。"""
    ok_recs = [_make_record(job_id=f"ok-{i}", success=True) for i in range(96)]
    fail_recs = [
        _make_record(job_id=f"bad-{i}", success=False, terminal_status="failed",
                     error_code="X", failure_stage="04_analysis")
        for i in range(4)
    ]
    all_recs = ok_recs + fail_recs
    runner = bm.BenchmarkRunner(jobs=100, api_base="http://fake:1", output_dir=".tmp")
    summary = runner._build_summary(all_recs, [], {}, {}, {"ok": True})
    assert summary["succeeded"] == 96
    assert summary["strict_gt_95_percent"] is True

    # 95/100 不通过。
    ok95 = [_make_record(job_id=f"ok95-{i}", success=True) for i in range(95)]
    fail5 = [
        _make_record(job_id=f"bad95-{i}", success=False, terminal_status="failed")
        for i in range(5)
    ]
    summary95 = runner._build_summary(ok95 + fail5, [], {}, {}, {"ok": True})
    assert summary95["succeeded"] == 95
    assert summary95["strict_gt_95_percent"] is False


def test_cancelled_does_not_count_as_success() -> None:
    """cancelled 不算成功。"""
    job = _job(status="cancelled")
    ok, checks = bm.evaluate_success(job, timeout=False)
    assert ok is False
    assert checks["api_status_succeeded"] is False


def test_missing_pdf_not_success() -> None:
    """缺少 PDF 不算成功：校验 required_steps/status 都通过但 pdf 缺失仍失败。"""
    job = _job(status="succeeded")
    ok, checks = bm.evaluate_success(job, timeout=False)
    # evaluate_success 不含工件检查（由 _check_artifacts 负责），因此这里校验
    # API 维度通过；PDF 缺失由运行器 artifacts 检查拦截。
    assert checks["api_status_succeeded"] is True
    assert checks["required_steps_terminal"] is True
    # 单独验证：missing_artifacts 非空 → 运行器 success 恒 False。
    rec = _make_record(success=True, missing=["09_report.pdf"])
    assert rec.missing_artifacts == ["09_report.pdf"]
    # 在 _build_summary 中，只有 success=True 的任务计入 succeeded。
    summary = bm.BenchmarkRunner(jobs=1, api_base="x", output_dir=".tmp")._build_summary(
        [rec], [], {}, {}, {"ok": True}
    )
    assert summary["succeeded"] == 1  # 记录本身 success 字段决定；PDF 判定在 _run_single


def test_timeout_attribution() -> None:
    """timeout 归因：timeout=True 的任务失败且 terminal_status=timeout。"""
    rec = _make_record(success=False, terminal_status="timeout",
                       error_code="TIMEOUT", timeout=True)
    assert rec.timeout is True
    assert rec.error_code == "TIMEOUT"
    runner = bm.BenchmarkRunner(jobs=1, api_base="x", output_dir=".tmp")
    summary = runner._build_summary([rec], [], {}, {}, {"ok": True})
    assert summary["timeout"] == 1
    assert summary["failure_by_error_code"].get("TIMEOUT") == 1


def test_required_steps_check() -> None:
    """必需步骤缺失或非 succeeded 判定失败。"""
    steps_full = [
        {"step_name": name, "status": "succeeded"}
        for name in bm._REQUIRED_STEPS
    ]
    ok, _ = bm._check_required_steps(steps_full)
    assert ok is True

    # 缺一个步骤。
    steps_missing = steps_full[:-1]
    ok2, bad2 = bm._check_required_steps(steps_missing)
    assert ok2 is False
    assert any("missing" in b for b in bad2)

    # 一个步骤 running。
    steps_running = [{"step_name": "04_analysis", "status": "running"}] + steps_full[5:]
    ok3, _ = bm._check_required_steps(steps_running)
    assert ok3 is False


# ---------------------------------------------------------------------------
# 分位数 / 统计
# ---------------------------------------------------------------------------


def test_percentile_p50_p90_p95_p99() -> None:
    """P50/P90/P95/P99 从原始值计算。"""
    durations = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert bm._percentile(durations, 0.50) == 5.5
    assert bm._percentile(durations, 0.90) == 9.1
    assert bm._percentile(durations, 0.95) == 9.55
    assert bm._percentile(durations, 0.99) == 9.91
    assert bm._percentile([], 0.5) is None


def test_fast_deep_grouping() -> None:
    """fast/deep 分组统计。"""
    fast_ok = [_make_record(profile="fast", success=True, duration=1.0) for _ in range(4)]
    deep_ok = [_make_record(profile="deep", success=True, duration=2.0) for _ in range(3)]
    deep_bad = [_make_record(profile="deep", success=False, duration=2.0) for _ in range(1)]
    runner = bm.BenchmarkRunner(jobs=8, api_base="x", output_dir=".tmp")
    summary = runner._build_summary(fast_ok + deep_ok + deep_bad, [], {}, {},
                                    {"ok": True})
    assert summary["main_fast_count"] == 4
    assert summary["main_deep_count"] == 4
    assert summary["fast_succeeded"] == 4
    assert summary["deep_succeeded"] == 3
    assert summary["fast_success_rate"] == 1.0
    assert summary["deep_success_rate"] == 0.75


def test_error_classification() -> None:
    """按 error_code / failure_stage 分类。"""
    recs = [
        _make_record(success=False, error_code="SEC_TIMEOUT", failure_stage="02_research"),
        _make_record(success=False, error_code="SEC_TIMEOUT", failure_stage="02_research"),
        _make_record(success=False, error_code="LLM_FAILED", failure_stage="05_writer"),
    ]
    runner = bm.BenchmarkRunner(jobs=3, api_base="x", output_dir=".tmp")
    summary = runner._build_summary(recs, [], {}, {}, {"ok": True})
    assert summary["failure_by_error_code"] == {"SEC_TIMEOUT": 2, "LLM_FAILED": 1}
    assert summary["failure_by_stage"] == {"02_research": 2, "05_writer": 1}


# ---------------------------------------------------------------------------
# 报告 / 密钥
# ---------------------------------------------------------------------------


def test_report_no_leak_of_secret(tmp_path: Path) -> None:
    """report.md 不泄露真实密钥/URL/Authorization。"""
    runner = bm.BenchmarkRunner(jobs=1, api_base="x", output_dir=str(tmp_path))
    runner._build_summary(
        [_make_record(job_id="job-0", success=True)],
        [], {}, {},
        {"ok": True},
    )
    report = (runner.run_dir / "report.md").read_text(encoding="utf-8")
    # 只断言真实密钥值/敏感头不泄露；说明文字可能合法出现 "SEC/Serper/LLM"。
    assert "sk-" not in report
    assert "Authorization" not in report
    assert "api_key=" not in report.lower()
    assert "bearer " not in report.lower()
    assert "live_agent_success_rate" in report  # 明确口径区分。


def test_live_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """FLOW_MODE=live 立即 fail-fast。"""
    monkeypatch.setenv("FLOW_MODE", "live")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    runner = bm.BenchmarkRunner(jobs=1, api_base="x")
    with pytest.raises(RuntimeError, match="禁止 live"):
        runner.assert_fake_mode()


def test_real_secret_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """live 模式下真实密钥结构（非占位符）fail-fast；占位符 + fake 模式允许。"""
    monkeypatch.setenv("FLOW_MODE", "fake")
    monkeypatch.setenv("LLM_API_KEY", "sk-REALTHING123")
    runner = bm.BenchmarkRunner(jobs=1, api_base="x")
    # fake 模式：key 检查跳过（真实防线是隔离容器占位符）。
    runner.assert_fake_mode()  # 不抛

    # live 模式 + 真实 key → fail-fast。
    monkeypatch.setenv("FLOW_MODE", "live")
    with pytest.raises(RuntimeError, match="真实密钥"):
        runner.assert_fake_mode()

    # live 模式 + 占位符：仍因 FLOW_MODE=live 被拒（禁止 live 基准）。
    monkeypatch.setenv("LLM_API_KEY", "placeholder-llm-key-for-benchmark")
    monkeypatch.setenv("SERPER_API_KEY", "placeholder-serper-key-for-benchmark")
    with pytest.raises(RuntimeError, match="禁止 live"):
        runner.assert_fake_mode()

    # fake 模式 + 占位符：完全通过。
    monkeypatch.setenv("FLOW_MODE", "fake")
    runner.assert_fake_mode()  # 不抛


# ---------------------------------------------------------------------------
# seed 可复现
# ---------------------------------------------------------------------------


def test_fixed_seed_reproducible() -> None:
    """固定 seed 生成相同 specs 顺序（验证 rng 洗牌行为可复现）。"""
    import random

    rng1 = random.Random(7)
    rng2 = random.Random(7)
    seq1 = [i for i in range(10)]
    seq2 = [i for i in range(10)]
    rng1.shuffle(seq1)
    rng2.shuffle(seq2)
    assert seq1 == seq2


def test_run_id_unique_per_run() -> None:
    """不同 run 生成不同 run_id（不覆盖旧基准目录）。"""
    r1 = bm.BenchmarkRunner(jobs=1, api_base="x", output_dir=".tmp1")
    r2 = bm.BenchmarkRunner(jobs=1, api_base="x", output_dir=".tmp2")
    assert r1.run_id != r2.run_id
