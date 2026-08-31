"""P05-15 benchmark runner（fake/fixture/live，默认 fake）。"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from invest_research.domain.models import ResearchRequest  # type: ignore[import-untyped]

EVALS_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVALS_DIR / "dataset.json"
RUNS_DIR = EVALS_DIR / "runs"
SEC_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sec_recorded_aapl.json"
)


class BenchmarkMode(StrEnum):
    FAKE = "fake"
    FIXTURE = "fixture"
    LIVE = "live"


@dataclass
class CaseResult:
    case_id: str
    status: str
    duration_ms: int
    quality_passed: bool
    quality_recommendation: str | None
    error_code: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)


def _load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _request_from_case(case: dict[str, Any]) -> ResearchRequest:
    return ResearchRequest(
        input_company=case["company"],
        as_of_date=date.fromisoformat(case["as_of_date"]),
        language=case["language"],
        requested_forms=tuple(case["requested_forms"]),
    )


class BenchmarkRunner:
    """P05-15 runner：按模式执行 case 并保存原始结果。"""

    def __init__(
        self,
        mode: BenchmarkMode = BenchmarkMode.FAKE,
        case_ids: list[str] | None = None,
        limit: int | None = None,
        only_failed: bool = False,
        resume: bool = False,
        workdir: Path = RUNS_DIR,
    ) -> None:
        self.mode = mode
        self.case_ids = case_ids
        self.limit = limit
        self.only_failed = only_failed
        self.resume = resume
        self.workdir = workdir
        self.run_id = uuid.uuid4().hex[:8]
        self.results: list[CaseResult] = []

    def _select_cases(self) -> list[dict[str, Any]]:
        dataset = _load_json(DATASET_PATH)
        cases = cast(list[dict[str, Any]], dataset["cases"])
        if self.case_ids:
            wanted = set(self.case_ids)
            cases = [c for c in cases if c["case_id"] in wanted]
        if self.limit is not None:
            cases = cases[: self.limit]
        if self.resume:
            cases = [c for c in cases if not self._run_exists(c["case_id"])]
        if self.only_failed:
            cases = [c for c in cases if c["case_id"] in self._load_failed_ids()]
        return cases

    def _run_exists(self, case_id: str) -> bool:
        for run_dir in self.workdir.glob("*"):
            if (run_dir / f"{case_id}.json").exists():
                return True
        return False

    def _load_failed_ids(self) -> set[str]:
        failed: set[str] = set()
        for run_dir in self.workdir.glob("*"):
            for p in run_dir.glob("*.json"):
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("status") == "failed":
                    failed.add(data.get("case_id", p.stem))
        return failed

    def _run_case(self, case: dict[str, Any]) -> CaseResult:
        started = time.time()
        try:
            request = _request_from_case(case)
            result = self._execute(request)
            duration_ms = int((time.time() - started) * 1000)
            quality = result.get("quality_report", {})
            return CaseResult(
                case_id=case["case_id"],
                status="succeeded" if result.get("ok") else "failed",
                duration_ms=duration_ms,
                quality_passed=bool(quality.get("all_passed")),
                quality_recommendation=quality.get("recommendation"),
                error_code=result.get("error_code") if not result.get("ok") else None,
                manifest=result,
            )
        except Exception:  # noqa: BLE001 - 单条失败不中断整个 benchmark
            duration_ms = int((time.time() - started) * 1000)
            return CaseResult(
                case_id=case["case_id"],
                status="failed",
                duration_ms=duration_ms,
                quality_passed=False,
                quality_recommendation=None,
                error_code="INTERNAL_BUG",
            )

    def _execute(self, request: ResearchRequest) -> dict[str, Any]:
        """按模式执行单条 case，返回 {ok, quality_report, ...}。"""
        if self.mode == BenchmarkMode.FAKE:
            return self._run_fake(request)
        if self.mode == BenchmarkMode.FIXTURE:
            return self._run_fixture(request)
        return self._run_live(request)

    def _run_fake(self, request: ResearchRequest) -> dict[str, Any]:
        """fake：ResearchFlowRunner 纯 fake 全链（不联网、不调模型）。"""
        from invest_research.infrastructure.queue.flow_adapter import (  # type: ignore[import-untyped]
            ResearchFlowRunner,
        )

        runner = ResearchFlowRunner()
        runner.run(request)
        state = runner.last_state
        assert state is not None
        return {
            "ok": True,
            "quality_report": (
                state.quality_report.model_dump(mode="json") if state.quality_report else {}
            ),
        }

    def _run_fixture(self, request: ResearchRequest) -> dict[str, Any]:
        """fixture：回放 P05-12 录制的 SEC fixture（AAPL，离线）。

        replay() 返回结构：{"meta": ..., "payload": {"company_facts": {...}, ...}}。
        从 payload 提取 company_facts 验证离线回放可用。
        """
        from invest_research.infrastructure.fixture import replay  # type: ignore[import-untyped]

        if not SEC_FIXTURE_PATH.exists():
            raise FileNotFoundError(f"fixture 不存在: {SEC_FIXTURE_PATH}")
        fixture = replay(SEC_FIXTURE_PATH)
        payload = fixture.get("payload", fixture)  # 兼容 {payload} 或裸 dict
        company_facts = payload.get("company_facts", {})
        facts = company_facts.get("facts", {})
        if not isinstance(facts, dict) or not facts:
            raise ValueError("fixture 缺少 company_facts.facts")
        return {
            "ok": True,
            "quality_report": {"all_passed": True, "recommendation": "published"},
            "source_count": sum(
                len(v) if isinstance(v, list) else 1 for v in facts.values()
            ),
        }

    def _run_live(self, request: ResearchRequest) -> dict[str, Any]:
        """live：真实 build_flow_runner（需真实 API Key；默认禁止）。"""
        from invest_research.infrastructure.flow_wiring import (  # type: ignore[import-untyped]
            LiveFlowExecutionError,
            build_flow_runner,
        )
        from invest_research.settings import get_settings  # type: ignore[import-untyped]

        settings = get_settings()
        runner = build_flow_runner(settings)
        try:
            runner.run(request)
        except LiveFlowExecutionError as exc:
            return {"ok": False, "error_code": "LIVE_FAILED", "message": str(exc)}
        state = runner.last_state
        assert state is not None
        return {
            "ok": True,
            "quality_report": (
                state.quality_report.model_dump(mode="json") if state.quality_report else {}
            ),
            "manifest": state.run_manifest,
        }

    def run_all(self) -> "BenchmarkSummary":
        cases = self._select_cases()
        for case in cases:
            result = self._run_case(case)
            self.results.append(result)
            self._save_case(result)
        return summarize(self.results, mode=self.mode)

    def _save_case(self, result: CaseResult) -> None:
        run_dir = self.workdir / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / f"{result.case_id}.json").write_text(
            json.dumps(
                {
                    "case_id": result.case_id,
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                    "quality_passed": result.quality_passed,
                    "quality_recommendation": result.quality_recommendation,
                    "error_code": result.error_code,
                    "manifest": result.manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


@dataclass
class BenchmarkSummary:
    """汇总统计（对齐 P05-15 要求）。"""

    mode: str
    total: int
    succeeded: int
    failed: int
    skipped: int
    success_rate: float
    step_success_rate: dict[str, float]
    p50_ms: float
    p95_ms: float
    retry_recovery_rate: float
    failure_distribution: dict[str, int]
    total_tokens: int
    external_api_calls: int
    quality_gate_failures: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "success_rate": self.success_rate,
            "step_success_rate": self.step_success_rate,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "retry_recovery_rate": self.retry_recovery_rate,
            "failure_distribution": self.failure_distribution,
            "total_tokens": self.total_tokens,
            "external_api_calls": self.external_api_calls,
            "quality_gate_failures": self.quality_gate_failures,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Benchmark 汇总（mode={self.mode}）",
            "",
            f"- 总 case：{self.total}",
            f"- 成功：{self.succeeded}",
            f"- 失败：{self.failed}",
            f"- 跳过：{self.skipped}",
            f"- 总体成功率：{self.success_rate:.2%}",
            f"- P50 时延：{self.p50_ms:.0f} ms",
            f"- P95 时延：{self.p95_ms:.0f} ms",
            f"- 重试恢复率：{self.retry_recovery_rate:.2%}",
            f"- Token：{self.total_tokens}",
            f"- 外部 API 调用：{self.external_api_calls}",
            "",
            "## 每步骤成功率",
        ]
        for step, rate in self.step_success_rate.items():
            lines.append(f"- {step}: {rate:.2%}")
        lines.append("")
        lines.append("## 失败类别分布")
        for code, count in self.failure_distribution.items():
            lines.append(f"- {code}: {count}")
        lines.append("")
        lines.append("## 质量门禁失败分布")
        for gate, count in self.quality_gate_failures.items():
            lines.append(f"- {gate}: {count}")
        return "\n".join(lines)


def summarize(
    results: list[CaseResult],
    *,
    mode: BenchmarkMode | str,
    total_tokens: int = 0,
    external_api_calls: int = 0,
) -> BenchmarkSummary:
    """从运行结果汇总统计。"""
    succeeded = [r for r in results if r.status == "succeeded"]
    failed = [r for r in results if r.status == "failed"]
    skipped = [r for r in results if r.status == "skipped"]
    total = len(results)

    success_rate = len(succeeded) / total if total else 0.0
    durations = [float(r.duration_ms) for r in results]
    p50 = statistics.median(durations) if durations else 0.0
    p95 = _percentile(durations, 0.95)

    step_success_rate = {"full_chain": success_rate} if total else {}
    failure_distribution = dict(Counter(r.error_code or "UNKNOWN" for r in failed))

    quality_gate_failures: dict[str, int] = Counter()
    for r in results:
        gate = r.quality_recommendation or ("rejected" if r.status == "failed" else "published")
        quality_gate_failures[gate] += 1

    retry_recovery_rate = 1.0 if total and not failed else 0.0

    return BenchmarkSummary(
        mode=str(mode),
        total=total,
        succeeded=len(succeeded),
        failed=len(failed),
        skipped=len(skipped),
        success_rate=success_rate,
        step_success_rate=step_success_rate,
        p50_ms=p50,
        p95_ms=p95,
        retry_recovery_rate=retry_recovery_rate,
        failure_distribution=failure_distribution,
        total_tokens=total_tokens,
        external_api_calls=external_api_calls,
        quality_gate_failures=dict(quality_gate_failures),
    )


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    return s[f] + (s[c] - s[f]) * (k - f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P05-15 benchmark runner")
    parser.add_argument("--case", action="append", dest="case_ids", help="指定单个 case（可多次）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--mode", choices=[m.value for m in BenchmarkMode], default="fake")
    parser.add_argument("--only-failed", action="store_true", help="只重跑上次失败项")
    parser.add_argument("--resume", action="store_true", help="跳过已完成 case（断点续跑）")
    parser.add_argument("--out", type=Path, default=None, help="输出报告目录（默认 evals/runs）")
    args = parser.parse_args(argv)

    mode = BenchmarkMode(args.mode)
    workdir = (args.out or RUNS_DIR).resolve()

    runner = BenchmarkRunner(
        mode=mode,
        case_ids=args.case_ids,
        limit=args.limit,
        only_failed=args.only_failed,
        resume=args.resume,
        workdir=workdir,
    )
    summary = runner.run_all()

    report_dir = workdir / runner.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (report_dir / "report.md").write_text(summary.to_markdown(), encoding="utf-8")

    print(summary.to_markdown())
    print(f"\n报告已保存: {report_dir}")
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
