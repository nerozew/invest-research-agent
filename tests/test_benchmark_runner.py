"""P05-15 benchmark runner 测试（离线）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.benchmark_runner import BenchmarkMode, BenchmarkRunner, CaseResult, summarize


def test_summarize_stats() -> None:
    rs = [
        CaseResult("a", "succeeded", 10, True, "published"),
        CaseResult("b", "succeeded", 20, True, "published"),
        CaseResult("c", "failed", 30, False, None, "RATE_LIMITED"),
        CaseResult("d", "failed", 40, False, None, "RATE_LIMITED"),
    ]
    s = summarize(rs, mode=BenchmarkMode.FAKE)
    assert (s.total, s.succeeded, s.failed) == (4, 2, 2)
    assert s.success_rate == 0.5
    assert s.p50_ms == 25.0
    assert s.p95_ms == 38.5
    assert s.failure_distribution == {"RATE_LIMITED": 2}
    assert s.retry_recovery_rate == 0.0


def test_summarize_p95() -> None:
    rs = [
        CaseResult(f"c{i}", "succeeded", d, True, "published")
        for i, d in enumerate([10, 20, 30, 40, 50])
    ]
    s = summarize(rs, mode="fake")
    assert (s.p50_ms, s.p95_ms) == (30.0, 48.0)
    assert s.retry_recovery_rate == 1.0


def test_runner_case_and_limit(tmp_path: Path) -> None:
    w = tmp_path / "runs"
    s1 = BenchmarkRunner(
        mode=BenchmarkMode.FAKE, case_ids=["aapl-annual_10k_zh"], workdir=w
    ).run_all()
    assert (s1.total, s1.succeeded) == (1, 1)
    s2 = BenchmarkRunner(mode=BenchmarkMode.FAKE, limit=3, workdir=w).run_all()
    assert (s2.total, s2.succeeded) == (3, 3)


def test_runner_resume_and_saves(tmp_path: Path) -> None:
    w = tmp_path / "runs"
    r1 = BenchmarkRunner(mode=BenchmarkMode.FAKE, limit=5, workdir=w)
    r1.run_all()
    run_dir = w / r1.run_id
    assert len(list(run_dir.glob("*.json"))) == 5
    data = json.loads(next(run_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert {"case_id", "status", "duration_ms"} <= set(data)
    s2 = BenchmarkRunner(mode=BenchmarkMode.FAKE, limit=5, resume=True, workdir=w).run_all()
    assert s2.total == 0
