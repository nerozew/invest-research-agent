"""P06-11 live portfolio benchmark 的离线测试（全部 fake HTTP/文件系统，不联网）。

覆盖任务第十二节要求的核心验收：dry-run 不联网、缺 --confirm-live 拒绝、
FLOW_MODE/占位符密钥拒绝、数据集校验、配套计划与顺序平衡、percentile/Wilson、
耗时计算、Prometheus delta 与 Counter 重置、Token 缺失不伪造 0、usage coverage、
成本计算/pricing 缺失、max-failures 熔断、resume、Ctrl+C 保存、工件与 PDF 校验、
三层成功口径、citation/as-of 检查、脱敏、report/claim eligibility、
load 结果不混入 paired 结论。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_live_portfolio_benchmark as lbm  # noqa: E402

REPORT_MD_BODY = "# AAPL report\n\n非空报告".encode("utf-8")


# ---------------------------------------------------------------------------
# Fake HTTP / Fake API 基础
# ---------------------------------------------------------------------------


def make_artifacts(
    *,
    recommendation: str = "published",
    all_passed: bool = True,
    completeness: str = "complete",
    citation_keys: list[str] | None = None,
    sec_url: str = "https://www.sec.gov/Archives/edgar/data/320193/000032019325000020/aapl-20250927.htm",
    locator: str = "Part II, Item 7",
    published_at: str = "2025-09-27",
    accessed_at: str = "2025-10-31",
    usage: dict[str, Any] | None = None,
) -> dict[str, bytes]:
    """构造合法工件集（默认能通过 full_quality）。"""
    if "sec.gov" in sec_url:
        research_pack = {
            "sources": [
                {
                    "canonical_url": sec_url,
                    "locator": locator,
                    "published_at": published_at,
                    "accessed_at": accessed_at,
                }
            ]
        }
    else:
        research_pack = {
            "sources": [
                {
                    "canonical_url": "https://example.com/a",
                    "locator": "p1",
                    "published_at": "2025-09-27",
                    "accessed_at": "2025-10-31",
                }
            ]
        }
    perf: dict[str, Any] = {
        "llm_calls": 5,
        "external_api_calls": 3,
        "retries": 0,
        "tool_cache": {"hits": 2, "misses": 1},
    }
    if usage is not None:
        # 真实 manifest 为 performance.token_usage。
        perf["token_usage"] = {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "cached_prompt_tokens": usage.get("cached_input_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
    manifest = {
        "status": "published",
        "models": {"research": "fake-model", "analysis": "fake-model", "writer": "fake-model"},
        "evidence": {"invocation_summary": {"sec_company_facts_calls": 1, "web_search_calls": 1}},
        "performance": perf,
    }
    return {
        "00_request.json": b"{}",
        "02_research_pack.json": json.dumps(research_pack).encode(),
        "04_financial_analysis_pack.json": json.dumps(
            {"completeness": completeness, "metrics": [], "facts": []}
        ).encode(),
        "05_report_draft.json": json.dumps(
            {
                "version": "report_draft_v1",
                "title": "t",
                "markdown": "# r",
                "citation_keys": citation_keys or ["src_abc", "fr_123"],
            }
        ).encode(),
        "06_quality_report.json": json.dumps(
            {
                "version": "quality_report_v1",
                "all_passed": all_passed,
                "issues": [],
                "warnings": [],
                "recommendation": recommendation,
            }
        ).encode(),
        "07_manifest.json": json.dumps(manifest).encode(),
        "08_report.md": REPORT_MD_BODY,
        "09_report.pdf": b"%PDF-1.4 fake",
    }


class FakeApi:
    """内存 fake API（鸭子类型，实现 run_one_job 所需方法）。"""

    def __init__(
        self, artifacts: dict[str, bytes] | None = None, status: str = "succeeded"
    ) -> None:
        self.artifacts = artifacts or make_artifacts()
        self.status = status
        self.created: list[dict[str, Any]] = []
        self.poll_count = 0

    def create_job(self, payload: dict[str, Any], key: str) -> tuple[int, dict[str, Any]]:
        self.created.append({"payload": payload, "key": key})
        return 202, {"job_id": "00000000-0000-0000-0000-000000000001", "status": "pending"}

    def get_job(self, job_id: str) -> tuple[int, dict[str, Any]]:
        if self.poll_count == 0:
            self.poll_count += 1
            return 200, {
                "job_id": job_id,
                "status": "running",
                "current_step": "02_research",
                "steps": [],
            }
        return 200, self._terminal_job(job_id)

    def _terminal_job(self, job_id: str) -> dict[str, Any]:
        steps = [
            {"step_name": n, "sequence_no": i, "status": "succeeded", "attempt_count": 1}
            for i, n in enumerate(
                (
                    "00_request",
                    "01_company_resolve",
                    "02_research",
                    "03_documents",
                    "04_analysis",
                    "05_writer",
                    "06_quality_gate",
                    "07_manifest",
                ),
                start=1,
            )
        ]
        return {
            "job_id": job_id,
            "status": self.status,
            "current_step": None,
            "research_profile": "fast",
            "error_code": None,
            "error_message": None,
            "failure_stage": None,
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:05:00+00:00",
            "duration_seconds": 300.0,
            "steps": steps,
        }

    def list_artifacts(self, job_id: str) -> tuple[int, list[str]]:
        return 200, sorted(self.artifacts.keys())

    def download_artifact(self, job_id: str, artifact_key: str) -> tuple[int, bytes]:
        content = self.artifacts.get(artifact_key)
        if content is None:
            return 404, b""
        return 200, content


class FakeProm:
    def __init__(
        self, before: dict[str, Any] | None = None, after: dict[str, Any] | None = None
    ) -> None:
        self.before = before if before is not None else {}
        self.after = after if after is not None else {}
        self.snapshot_count = 0

    def healthy(self) -> bool:
        return True

    def snapshot(self) -> dict[str, Any]:
        self.snapshot_count += 1
        if self.snapshot_count == 1:
            return dict(self.before)
        return dict(self.after)

    def delta(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        return {
            "llm_requests_total": {
                "sum": {
                    "labels": {"status": "success"},
                    "before": 10.0,
                    "after": 12.0,
                    "delta": 2.0,
                }
            }
        }

    def detect_counter_resets(self, delta: dict[str, Any]) -> list[str]:
        return []


def make_plan(
    case: dict[str, Any] | None = None, profile: str = "fast", repetition: int = 1
) -> lbm.JobPlan:
    case = case or {
        "case_id": "live-aapl",
        "ticker": "AAPL",
        "sector": "technology",
        "as_of_date": "2025-10-31",
        "language": "zh-CN",
        "requested_forms": ["10-K", "10-Q"],
    }
    return lbm.JobPlan(
        suite="paired",
        case=case,
        profile=profile,
        repetition=repetition,
        pair_id=f"live-{case['ticker']}-r{repetition}",
        execution_order=1,
        configured_concurrency=1,
    )


def make_annual_plan(repetition: int = 1) -> lbm.JobPlan:
    return lbm.JobPlan(
        suite="annual-paired",
        case={
            "case_id": "annual-amzn",
            "ticker": "AMZN",
            "sector": "ecommerce",
            "as_of_date": "2025-10-31",
            "language": "zh-CN",
            "requested_forms": ["10-K"],
        },
        profile="deep",
        research_mode="annual_deep",
        repetition=repetition,
        pair_id=f"annual-AMZN-r{repetition}",
        execution_order=1,
        configured_concurrency=1,
    )


def make_annual_artifacts(*, partial: bool = False) -> dict[str, bytes]:
    artifacts = make_artifacts(recommendation="publish_partial" if partial else "published")
    artifacts["05_report_draft.json"] = json.dumps(
        {"version": "annual_report_draft_v1", "citation_keys": ["src_annual", "fr_annual"]}
    ).encode()
    artifacts["08_report.md"] = (
        b"# Annual\n\n## \xe6\x95\xb0\xe6\x8d\xae\xe9\x99\x90\xe5\x88\xb6\n- limited"
    )
    artifacts["annual/runtime_state.json"] = json.dumps(
        {
            "schema_version": "annual_runtime_state_v1",
            "finalization_status": (
                "partial_ready_for_final_writer" if partial else "ready_for_final_writer"
            ),
        }
    ).encode()
    artifacts["annual/company-facts/selected.json"] = json.dumps(
        {"facts": [{"concept": "Revenue"}]}
    ).encode()
    artifacts["annual/company-facts/manifest.json"] = json.dumps({"status": "completed"}).encode()
    artifacts["annual/comparison.json"] = json.dumps(
        {
            "status": "partial" if partial else "ready",
            "metrics": [{"name": str(i)} for i in range(10)],
        }
    ).encode()
    for key in lbm.ANNUAL_SECTION_ARTIFACTS:
        artifacts[key] = json.dumps(
            {"draft": {"status": "partial" if partial else "ready"}}
        ).encode()
    return artifacts


class FakeAnnualApi(FakeApi):
    def _terminal_job(self, job_id: str) -> dict[str, Any]:
        job = super()._terminal_job(job_id)
        job["research_mode"] = "annual_deep"
        job["annual_nodes"] = {
            "critical_path_seconds": 12.5,
            "nodes": [
                {"node_key": "annual_selection", "status": "succeeded"},
                {"node_key": "annual_final_writer", "status": "succeeded"},
            ],
        }
        return job


class TestAnnualPairedEvaluation:
    def test_plan_is_deep_only_and_alternates_modes(self) -> None:
        first = make_annual_plan().case
        cases = [first, {**first, "case_id": "annual-jpm", "ticker": "JPM"}]
        plans = lbm.build_annual_paired_plan(cases, repeats=2, seed=7)
        assert len(plans) == 8
        assert {plan.profile for plan in plans} == {"deep"}
        assert {plan.research_mode for plan in plans} == {"legacy", "annual_deep"}
        assert [plan.research_mode for plan in plans[:2]] == ["annual_deep", "legacy"]
        assert [plan.research_mode for plan in plans[2:4]] == ["legacy", "annual_deep"]
        assert lbm.job_payload(plans[0])["requested_forms"] == ["10-K"]
        assert lbm.job_payload(plans[0])["research_mode"] == "annual_deep"
        assert "annual_deep" in lbm.idempotency_key("r", plans[0])

    def test_annual_delivery_and_partial_delivery_are_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lbm.time, "sleep", lambda *args, **kwargs: None)
        for partial in (False, True):
            record = lbm.run_one_job(
                FakeAnnualApi(artifacts=make_annual_artifacts(partial=partial)),
                plan=make_annual_plan(),
                run_id="annual-test",
                timeout_s=30,
                max_total_tokens=0,
                token_budget={"used": 0},
            )
            assert record["technical_success"] is True
            assert record["live_acceptance_success"] is True
            assert record["annual_partial_delivery"] is partial
            assert record["annual_critical_path_seconds"] == 12.5

    def test_annual_blocked_evidence_never_becomes_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lbm.time, "sleep", lambda *args, **kwargs: None)
        artifacts = make_annual_artifacts()
        del artifacts["annual/company-facts/selected.json"]
        record = lbm.run_one_job(
            FakeAnnualApi(artifacts=artifacts),
            plan=make_annual_plan(),
            run_id="annual-test",
            timeout_s=30,
            max_total_tokens=0,
            token_budget={"used": 0},
        )
        assert record["technical_success"] is False
        assert "missing_artifact:annual/company-facts/selected.json" in record[
            "evaluate_technical_failures"
        ]

    def test_annual_resume_identity_includes_mode(self, tmp_path: Path) -> None:
        (tmp_path / "jobs.jsonl").write_text(
            json.dumps(
                {
                    "terminal_status": "succeeded",
                    "suite": "annual-paired",
                    "case_id": "annual-amzn",
                    "profile": "deep",
                    "research_mode": "legacy",
                    "ticker": "AMZN",
                    "repetition": 1,
                    "pair_id": "annual-AMZN-r1",
                    "configured_concurrency": 1,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        existing = lbm.read_existing_records(tmp_path)
        assert make_annual_plan().identity() not in existing


def base_cases() -> list[dict[str, Any]]:
    data = json.loads(
        (Path(__file__).resolve().parents[1] / "evals" / "live_dataset.json").read_text("utf-8")
    )
    return data["cases"]


# ---------------------------------------------------------------------------
# dry-run / live 环境校验
# ---------------------------------------------------------------------------


class TestDryRunAndLiveGuard:
    def test_default_dry_run_does_not_touch_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail_api(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("dry-run 不应创建任何 HTTP 客户端")

        monkeypatch.setattr(lbm, "ApiClient", _fail_api)
        monkeypatch.setattr(lbm, "PrometheusClient", _fail_api)
        assert lbm.main(["--suite", "smoke"]) == 0

    def test_without_confirm_live_is_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert lbm.main(["--suite", "smoke"]) == 0
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "不联网" in out

    def test_explicit_dry_run_flag_is_safe(self) -> None:
        assert lbm.main(["--suite", "paired", "--repeats", "3", "--dry-run"]) == 0

    def test_annual_paired_dry_run_uses_fixed_dataset_and_eight_jobs(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert lbm.main(["--suite", "annual-paired", "--repeats", "2", "--dry-run"]) == 0
        output = capsys.readouterr().out
        assert "计划任务数: 8" in output
        assert "/deep/legacy/" in output
        assert "/deep/annual_deep/" in output

    def test_flow_mode_not_live_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = SimpleNamespace(
            flow_mode="fake",
            llm_api_key=SimpleNamespace(get_secret_value=lambda: "sk-real-1234567890"),
            serper_api_key=SimpleNamespace(get_secret_value=lambda: "real-serper-key"),
        )
        monkeypatch.setattr(lbm, "load_settings", lambda: settings)
        assert lbm.main(["--suite", "smoke", "--confirm-live"]) == 5

    def test_placeholder_secret_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = SimpleNamespace(
            flow_mode="live",
            llm_api_key=SimpleNamespace(get_secret_value=lambda: "your-llm-key-here"),
            serper_api_key=SimpleNamespace(get_secret_value=lambda: "xxx"),
        )
        monkeypatch.setattr(lbm, "load_settings", lambda: settings)
        assert lbm.main(["--suite", "smoke", "--confirm-live"]) == 5


# ---------------------------------------------------------------------------
# 数据集
# ---------------------------------------------------------------------------


class TestDataset:
    def test_load_live_dataset_schema(self) -> None:
        cases = lbm.load_live_dataset(
            Path(__file__).resolve().parents[1] / "evals" / "live_dataset.json"
        )
        assert len(cases) == 10
        tickers = {c["ticker"] for c in cases}
        assert tickers == {"AAPL", "MSFT", "AMZN", "JPM", "JNJ", "WMT", "XOM", "BA", "KO", "TSLA"}

    def test_validate_dataset_ok(self) -> None:
        assert lbm.validate_dataset(base_cases()) == []

    def test_resume_dataset_excludes_overused_companies_and_resolves_locally(self) -> None:
        from invest_research.tools.base import ToolSuccess
        from invest_research.tools.company_resolver import (
            CompanyResolverTool,
            ResolveCompanyRequest,
        )

        path = (
            Path(__file__).resolve().parents[1]
            / "evals"
            / "live_dataset_no_aapl_msft.json"
        )
        cases = lbm.load_live_dataset(path)
        tickers = {str(case["ticker"]) for case in cases}

        assert len(cases) == 10
        assert "AAPL" not in tickers
        assert "MSFT" not in tickers
        assert "XOM" not in tickers  # 避免当前主体 CIK 与 2025 as-of 的历史映射混杂
        assert lbm.validate_dataset(cases) == []
        resolver = CompanyResolverTool()
        for ticker in tickers:
            result = resolver.execute(ResolveCompanyRequest(input_company=ticker))
            assert isinstance(result, ToolSuccess), ticker
            assert result.value.resolved is True, ticker

    def test_validate_dataset_errors(self) -> None:
        bad = [
            {
                "ticker": "AAPL",
                "sector": "x",
                "as_of_date": "2025-10-31",
                "language": "zh-CN",
                "requested_forms": ["10-K", "10-Q"],
            },
            {
                "case_id": "a",
                "ticker": "AAPL",
                "sector": "x",
                "as_of_date": "2025-10-31",
                "language": "zh-CN",
                "requested_forms": ["10-K", "10-Q"],
            },
            {
                "case_id": "b",
                "ticker": "MSFT",
                "sector": "x",
                "as_of_date": "bad-date",
                "language": "en",
                "requested_forms": ["10-K"],
            },
        ]
        errors = lbm.validate_dataset(bad)
        assert any("case_id" in e for e in errors)
        assert any("重复" in e for e in errors)
        assert any("as_of_date" in e for e in errors)
        assert any("language" in e for e in errors)
        assert any("requested_forms" in e for e in errors)


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------


class TestPlans:
    def test_paired_plan_count_and_balance(self) -> None:
        cases = base_cases()
        plans = lbm.build_paired_plan(cases, repeats=3, seed=7)
        assert len(plans) == 10 * 2 * 3
        rep1 = [p for p in plans if p.repetition == 1]
        first_profiles = [rep1[i * 2].profile for i in range(10)]
        assert first_profiles.count("fast") == 5
        assert first_profiles.count("deep") == 5
        rep2 = [p for p in plans if p.repetition == 2]
        rep2_first = [rep2[i * 2].profile for i in range(10)]
        assert rep2_first == ["deep" if p == "fast" else "fast" for p in first_profiles]

    def test_smoke_plan(self) -> None:
        plans = lbm.build_smoke_plan(base_cases(), seed=7)
        assert len(plans) == 4
        assert {p.profile for p in plans} == {"fast", "deep"}
        assert all(p.configured_concurrency == 1 for p in plans)

    def test_resume_dataset_two_repeats_is_20_pairs_40_jobs(self) -> None:
        cases = lbm.load_live_dataset(
            Path(__file__).resolve().parents[1]
            / "evals"
            / "live_dataset_no_aapl_msft.json"
        )
        plans = lbm.build_paired_plan(cases, repeats=2, seed=7)

        assert len(plans) == 40
        assert len({plan.pair_id for plan in plans}) == 20
        assert all(plan.configured_concurrency == 1 for plan in plans)
        for pair_id in {plan.pair_id for plan in plans}:
            profiles = {plan.profile for plan in plans if plan.pair_id == pair_id}
            assert profiles == {"fast", "deep"}

    def test_load_plan_split(self) -> None:
        cases = base_cases()
        plans = lbm.build_load_plan(cases, [1, 2, 4], jobs_per_level=8, profile="fast", seed=7)
        assert len(plans) == 3 * 8
        levels = {p.configured_concurrency for p in plans}
        assert levels == {1, 2, 4}
        assert all(p.profile == "fast" for p in plans)
        assert all("load" in p.pair_id for p in plans)

    def test_idempotency_key_stable(self) -> None:
        plan = make_plan(profile="fast", repetition=2)
        key1 = lbm.idempotency_key("run1", plan)
        key2 = lbm.idempotency_key("run1", plan)
        assert key1 == key2
        assert "run1" in key1 and "live-aapl" in key1 and "fast" in key1 and "r2" in key1

    def test_job_payload_uses_input_company(self) -> None:
        plan = make_plan()
        payload = lbm.job_payload(plan)
        assert payload["input_company"] == "AAPL"
        assert payload["research_profile"] == "fast"
        assert payload["requested_forms"] == ["10-K", "10-Q"]
        assert "ticker" not in payload


# ---------------------------------------------------------------------------
# 统计工具
# ---------------------------------------------------------------------------


class TestStats:
    def test_percentile(self) -> None:
        vals = [1.0, 2.0, 3.0, 4.0]
        assert lbm._percentile(vals, 0.0) == 1.0
        assert lbm._percentile(vals, 1.0) == 4.0
        assert lbm._percentile(vals, 0.5) == 2.5
        assert lbm._percentile([], 0.5) is None

    def test_aggregate_durations(self) -> None:
        recs = [
            {"e2e_duration_seconds": 100.0},
            {"e2e_duration_seconds": 200.0},
            {"e2e_duration_seconds": None},
            {"e2e_duration_seconds": 400.0},
        ]
        agg = lbm.aggregate_durations(recs, "e2e_duration_seconds")
        assert agg["n"] == 3
        assert agg["avg"] == pytest.approx(700.0 / 3)
        assert agg["min"] == 100.0
        assert agg["max"] == 400.0
        assert agg["p50"] == 200.0

    def test_wilson_interval_edges(self) -> None:
        assert lbm.wilson_interval(0, 0) == {"lo": None, "hi": None}
        assert lbm.wilson_interval(0, 10) == {"lo": 0.0, "hi": 0.0}
        assert lbm.wilson_interval(10, 10) == {"lo": 1.0, "hi": 1.0}
        lo = lbm.wilson_interval(95, 100)["lo"]
        hi = lbm.wilson_interval(95, 100)["hi"]
        assert lo is not None and 0.88 <= lo <= 0.90
        assert hi is not None and 0.97 <= hi <= 0.99

    def test_pct_change(self) -> None:
        assert lbm.pct_change(200.0, 150.0) == 25.0
        assert lbm.pct_change(200.0, 200.0) == 0.0
        assert lbm.pct_change(0.0, 100.0) is None
        assert lbm.pct_change(None, 100.0) is None

    def test_queue_execution_computed_from_fake_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(lbm.time, "sleep", lambda *a, **k: None)
        api = FakeApi()
        rec = lbm.run_one_job(
            api,
            plan=make_plan(),
            run_id="run-x",
            timeout_s=30,
            max_total_tokens=0,
            token_budget={"used": 0},
        )
        assert rec["queue_duration_seconds"] is not None
        assert rec["execution_duration_seconds"] == 300.0
        assert rec["e2e_duration_seconds"] is not None
        assert rec["queue_duration_seconds"] <= rec["e2e_duration_seconds"]

    def test_idempotent_replay_200_recovers_existing_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """相同幂等请求返回 200 + 原 job_id 时继续轮询，不误判提交失败。"""
        monkeypatch.setattr(lbm.time, "sleep", lambda *a, **k: None)

        class ReplayApi(FakeApi):
            def create_job(
                self, payload: dict[str, Any], key: str
            ) -> tuple[int, dict[str, Any]]:
                self.created.append({"payload": payload, "key": key})
                return 200, {
                    "job_id": "00000000-0000-0000-0000-000000000001",
                    "status": "pending",
                }

        rec = lbm.run_one_job(
            ReplayApi(),
            plan=make_plan(),
            run_id="run-x",
            timeout_s=30,
            max_total_tokens=0,
            token_budget={"used": 0},
        )
        assert rec["job_id"] == "00000000-0000-0000-0000-000000000001"
        assert rec["terminal_status"] == "succeeded"
        assert rec["technical_success"] is True
        assert rec["error_code"] is None
        assert rec["idempotency_replayed"] is True
        assert rec["queue_duration_seconds"] is None
        assert rec["e2e_duration_seconds"] is None


# ---------------------------------------------------------------------------
# 三层成功口径 / 工件 / citation / as-of
# ---------------------------------------------------------------------------


class TestSuccessLevels:
    def _success_job(self) -> dict[str, Any]:
        job = FakeApi()._terminal_job("00000000-0000-0000-0000-000000000001")
        job["status"] = "succeeded"
        return job

    def test_technical_success_requires_artifacts(self) -> None:
        job = self._success_job()
        artifacts = make_artifacts()
        del artifacts["09_report.pdf"]
        ok, failures = lbm.evaluate_technical_success(job, artifacts=artifacts, timeout_hit=False)
        assert not ok
        assert any("missing_artifact:09_report.pdf" in f for f in failures)

    def test_pdf_header_check(self) -> None:
        job = self._success_job()
        artifacts = make_artifacts()
        artifacts["09_report.pdf"] = b"not-a-pdf"
        ok, failures = lbm.evaluate_technical_success(job, artifacts=artifacts, timeout_hit=False)
        assert not ok
        assert any("pdf_bad_header" in f for f in failures)

    def test_technical_success_rejects_running_step(self) -> None:
        job = self._success_job()
        job["steps"][2]["status"] = "running"
        ok, failures = lbm.evaluate_technical_success(
            job, artifacts=make_artifacts(), timeout_hit=False
        )
        assert not ok
        assert any("running_step_present" in f or "step_not_succeeded" in f for f in failures)

    def test_live_acceptance_requires_live_evidence(self) -> None:
        job = self._success_job()
        artifacts = make_artifacts()
        manifest = json.loads(artifacts["07_manifest.json"])
        manifest["evidence"]["invocation_summary"] = {}
        artifacts["07_manifest.json"] = json.dumps(manifest).encode()
        ok, failures = lbm.evaluate_live_acceptance(
            job,
            quality_report=json.loads(artifacts["06_quality_report.json"]),
            manifest=manifest,
            research_pack=json.loads(artifacts["02_research_pack.json"]),
            report_draft=json.loads(artifacts["05_report_draft.json"]),
            as_of_date="2025-10-31",
            artifacts=artifacts,
        )
        assert not ok
        assert any("invocation_summary" in f for f in failures)

    def test_full_quality_requires_published_and_complete(self) -> None:
        artifacts = make_artifacts(recommendation="publish_partial", completeness="partial")
        quality = json.loads(artifacts["06_quality_report.json"])
        analysis = json.loads(artifacts["04_financial_analysis_pack.json"])
        ok, failures = lbm.evaluate_full_quality(True, quality, analysis)
        assert not ok
        assert any("recommendation_not_published" in f for f in failures)
        assert any("analysis_completeness" in f for f in failures)

    def test_full_quality_pass(self) -> None:
        artifacts = make_artifacts()
        quality = json.loads(artifacts["06_quality_report.json"])
        analysis = json.loads(artifacts["04_financial_analysis_pack.json"])
        ok, failures = lbm.evaluate_full_quality(True, quality, analysis)
        assert ok and failures == []

    def test_three_levels_are_strictly_ordered(self) -> None:
        art = make_artifacts(recommendation="publish_partial", completeness="partial")
        job = self._success_job()
        tech_ok, _ = lbm.evaluate_technical_success(job, artifacts=art, timeout_hit=False)
        live_ok, _ = lbm.evaluate_live_acceptance(
            job,
            quality_report=json.loads(art["06_quality_report.json"]),
            manifest=json.loads(art["07_manifest.json"]),
            research_pack=json.loads(art["02_research_pack.json"]),
            report_draft=json.loads(art["05_report_draft.json"]),
            as_of_date="2025-10-31",
            artifacts=art,
        )
        full_ok, _ = lbm.evaluate_full_quality(
            live_ok,
            json.loads(art["06_quality_report.json"]),
            json.loads(art["04_financial_analysis_pack.json"]),
        )
        assert tech_ok is True
        assert live_ok is True
        assert full_ok is False

    def test_citation_validation(self) -> None:
        keys = ["src_abc", "fr_123", "fake_key"]
        assert lbm._count_citations({"citation_keys": keys}) == {
            "citation_count": 3,
            "valid_citation_count": 2,
        }

    def test_official_sec_citation_count(self) -> None:
        research_pack = {
            "sources": [
                {"canonical_url": "https://www.sec.gov/Archives/x.htm", "locator": "Item 7"},
                {"canonical_url": "https://www.sec.gov/Archives/y.htm", "locator": ""},
                {"canonical_url": "https://example.com/a", "locator": "p1"},
            ]
        }
        assert lbm._official_sec_source_count(research_pack) == 1

    def test_as_of_compliance(self) -> None:
        research_pack = {
            "sources": [
                {
                    "canonical_url": "https://www.sec.gov/x",
                    "locator": "l",
                    "published_at": "2025-11-01",
                    "accessed_at": "2025-11-01",
                },
            ]
        }
        assert lbm._as_of_compliance(research_pack, "2025-10-31") is False

    def test_as_of_compliance_ok(self) -> None:
        research_pack = {
            "sources": [
                {
                    "canonical_url": "https://www.sec.gov/x",
                    "locator": "l",
                    "published_at": "2025-09-27",
                    "accessed_at": "2025-10-31",
                },
            ]
        }
        assert lbm._as_of_compliance(research_pack, "2025-10-31") is True


# ---------------------------------------------------------------------------
# Prometheus / Token / 成本
# ---------------------------------------------------------------------------


def _prom_resp(value: float, labels: dict[str, str] | None = None) -> dict[str, Any]:
    return {"labels": labels or {}, "value": [str(time.time()), str(value)]}


class TestPrometheusAndToken:
    def test_snapshot_delta_keeps_labels(self) -> None:
        before = {"llm_requests_total": [_prom_resp(10.0, {"status": "success"})]}
        after = {"llm_requests_total": [_prom_resp(12.0, {"status": "success"})]}
        prom = lbm.PrometheusClient("http://fake", http=httpx.Client())
        diff = prom.delta(before, after)
        entry = diff["llm_requests_total"]
        key = next(iter(entry))
        assert entry[key]["delta"] == 2.0
        assert entry[key]["labels"]["status"] == "success"

    def test_counter_reset_detected(self) -> None:
        prom = lbm.PrometheusClient("http://fake", http=httpx.Client())
        before = {"llm_requests_total": [_prom_resp(15.0, {"status": "success"})]}
        after = {"llm_requests_total": [_prom_resp(5.0, {"status": "success"})]}
        resets = prom.detect_counter_resets(prom.delta(before, after))
        assert len(resets) == 1
        assert "llm_requests_total" in resets[0]

    def test_usage_missing_not_faked_as_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(lbm.time, "sleep", lambda *a, **k: None)
        art = make_artifacts(usage=None)
        api = FakeApi(artifacts=art)
        rec = lbm.run_one_job(
            api,
            plan=make_plan(),
            run_id="run-x",
            timeout_s=30,
            max_total_tokens=0,
            token_budget={"used": 0},
        )
        assert rec["total_tokens"] is None
        assert rec["token_usage_complete"] is False

    def test_usage_present_is_recorded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(lbm.time, "sleep", lambda *a, **k: None)
        art = make_artifacts(
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_input_tokens": 10,
                "total_tokens": 150,
            }
        )
        api = FakeApi(artifacts=art)
        rec = lbm.run_one_job(
            api,
            plan=make_plan(),
            run_id="run-x",
            timeout_s=30,
            max_total_tokens=0,
            token_budget={"used": 0},
        )
        assert rec["total_tokens"] == 150
        assert rec["token_usage_complete"] is True

    def test_usage_coverage(self) -> None:
        records = [
            {
                "suite": "paired",
                "token_usage_complete": True,
                "profile": "fast",
                "ticker": "A",
                "sector": "s",
                "case_id": "c",
                "repetition": 1,
                "pair_id": "p1",
                "live_acceptance_success": True,
                "technical_success": True,
                "full_quality_success": True,
                "official_sec_citation_count": 1,
                "e2e_duration_seconds": 10,
            },
            {
                "suite": "paired",
                "token_usage_complete": False,
                "profile": "deep",
                "ticker": "A",
                "sector": "s",
                "case_id": "c",
                "repetition": 1,
                "pair_id": "p1",
                "live_acceptance_success": True,
                "technical_success": True,
                "full_quality_success": True,
                "official_sec_citation_count": 1,
                "e2e_duration_seconds": 20,
            },
        ]
        eligibility = lbm._claim_eligibility(records, repeats=1)
        assert eligibility["token_usage_coverage"] == 0.5
        assert eligibility["claim_eligible"] is False

    def test_20_pair_resume_design_is_explicit_and_eligible_when_quality_holds(self) -> None:
        records: list[dict[str, Any]] = []
        for company_index in range(10):
            ticker = f"T{company_index}"
            for repetition in (1, 2):
                pair_id = f"{ticker}-r{repetition}"
                for profile in ("fast", "deep"):
                    records.append(
                        {
                            "suite": "paired",
                            "profile": profile,
                            "ticker": ticker,
                            "sector": "test",
                            "case_id": ticker,
                            "repetition": repetition,
                            "pair_id": pair_id,
                            "token_usage_complete": True,
                            "live_acceptance_success": True,
                            "technical_success": True,
                            "full_quality_success": True,
                            "e2e_duration_seconds": 10 if profile == "fast" else 20,
                            "total_tokens": 100 if profile == "fast" else 200,
                        }
                    )

        original_design = lbm._claim_eligibility(records, repeats=2)
        resume_design = lbm._claim_eligibility(
            records,
            repeats=2,
            recommended_repeats=2,
            minimum_complete_pairs=20,
            minimum_unique_companies=10,
        )

        assert original_design["claim_eligible"] is False
        assert resume_design["claim_eligible"] is True
        assert resume_design["completed_fast_deep_pairs"] == 20
        assert resume_design["unique_companies_with_complete_pairs"] == 10

    def test_cost_null_without_pricing_file(self) -> None:
        cost = lbm._cost_summary([], None)
        assert cost["estimated_cost_usd"] is None
        assert "不在代码中硬编码模型价格" in cost["note"]

    def test_cost_calculation(self, tmp_path: Path) -> None:
        pricing = tmp_path / "pricing.json"
        pricing.write_text(
            json.dumps(
                {
                    "as_of": "2026-01-01",
                    "models": {
                        "fast": {"input_per_1m": 0.5, "output_per_1m": 1.5},
                        "default": {"input_per_1m": 0.5, "output_per_1m": 1.5},
                    },
                }
            ),
            encoding="utf-8",
        )
        records = [
            {
                "profile": "fast",
                "total_tokens": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "technical_success": True,
            },
        ]
        cost = lbm._cost_summary(records, pricing)
        assert cost["estimated_cost_usd"] == pytest.approx(
            (800 * 0.5 + 200 * 1.5) / 1_000_000, abs=1e-8
        )
        assert cost["per_successful_job_avg_usd"] is not None


# ---------------------------------------------------------------------------
# 熔断 / resume / Ctrl+C
# ---------------------------------------------------------------------------


class TestRecovery:
    def _runner(
        self, tmp_path: Path, api: FakeApi, prom: FakeProm | None = None
    ) -> lbm.LiveBenchmarkRunner:
        return lbm.LiveBenchmarkRunner(
            suite="paired",
            plans=[
                make_plan(profile="fast", repetition=1),
                make_plan(profile="deep", repetition=1),
            ],
            api=api,
            prom=prom or FakeProm(),
            run_dir=tmp_path,
            timeout_s=30,
            max_failures=2,
            max_total_tokens=0,
            cooldown_s=0,
            resume_existing=set(),
            summary_config={"run_id": "run-x"},
        )

    def test_max_failures_stops_submitting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lbm.time, "sleep", lambda *a, **k: None)
        api = FakeApi(status="failed")
        runner = self._runner(tmp_path, api)
        runner.max_failures = 2
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lbm, "write_json", lambda *a, **k: None)
            mp.setattr(lbm, "write_jsonl", lambda *a, **k: None)
            runner.run()
        assert len(runner.records) == 2
        assert runner._stop is True

    def test_max_failures_allows_one_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lbm.time, "sleep", lambda *a, **k: None)
        api = FakeApi(status="failed")
        runner = self._runner(tmp_path, api)
        runner.max_failures = 2
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lbm, "write_json", lambda *a, **k: None)
            mp.setattr(lbm, "write_jsonl", lambda *a, **k: None)
            runner.run()
        assert len(runner.records) == 2

    def test_resume_skips_existing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(lbm.time, "sleep", lambda *a, **k: None)
        existing_jobs = tmp_path / "jobs.jsonl"
        existing_jobs.write_text(
            json.dumps(
                {
                    "terminal_status": "succeeded",
                    "suite": "paired",
                    "case_id": "live-aapl",
                    "profile": "fast",
                    "ticker": "AAPL",
                    "repetition": 1,
                    "pair_id": "live-AAPL-r1",
                    "configured_concurrency": 1,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        resume_set = lbm.read_existing_records(tmp_path)
        # 新格式记录（含 suite/pair_id/concurrency）→ 7 元组身份完整匹配。
        assert ("paired", "live-aapl", "fast", "AAPL", 1, "live-AAPL-r1", 1) in resume_set
        assert ("", "live-aapl", "fast", "AAPL", 1, "", 1) not in resume_set  # 旧格式不回退
        api = FakeApi()
        runner = lbm.LiveBenchmarkRunner(
            suite="paired",
            plans=[
                make_plan(profile="fast", repetition=1),
                make_plan(profile="deep", repetition=1),
            ],
            api=api,
            prom=FakeProm(),
            run_dir=tmp_path,
            timeout_s=30,
            max_failures=2,
            max_total_tokens=0,
            cooldown_s=0,
            resume_existing=resume_set,
            summary_config={"run_id": "run-x"},
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lbm, "write_json", lambda *a, **k: None)
            mp.setattr(lbm, "write_jsonl", lambda *a, **k: None)
            runner.run()
        created_keys = [c["key"] for c in api.created]
        assert len(created_keys) == 1
        assert "deep" in created_keys[0]

    def test_resume_retries_client_submit_error_without_job_id(
        self, tmp_path: Path
    ) -> None:
        """客户端提交伪失败不是服务端终态；稳定幂等键允许安全恢复。"""
        (tmp_path / "jobs.jsonl").write_text(
            json.dumps(
                {
                    "terminal_status": "failed",
                    "error_code": "CLIENT_SUBMIT_ERROR",
                    "failure_stage": "client_submit",
                    "job_id": None,
                    "suite": "paired",
                    "case_id": "live-aapl",
                    "profile": "fast",
                    "ticker": "AAPL",
                    "repetition": 1,
                    "pair_id": "live-AAPL-r1",
                    "configured_concurrency": 1,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert lbm.read_existing_records(tmp_path) == set()

    def test_merge_records_replaces_client_failure_with_recovered_job(self) -> None:
        identity = {
            "suite": "smoke",
            "case_id": "live-msft",
            "profile": "fast",
            "ticker": "MSFT",
            "repetition": 1,
            "pair_id": None,
            "configured_concurrency": 1,
        }
        old = {
            **identity,
            "terminal_status": "failed",
            "error_code": "CLIENT_SUBMIT_ERROR",
            "job_id": None,
        }
        recovered = {
            **identity,
            "terminal_status": "succeeded",
            "error_code": None,
            "job_id": "job-existing",
        }
        merged = lbm.merge_records([old], [recovered])
        assert len(merged) == 1
        assert merged[0]["job_id"] == "job-existing"
        assert merged[0]["terminal_status"] == "succeeded"

    def test_ctrl_c_saves_partial(self, tmp_path: Path) -> None:
        api = FakeApi()
        runner = self._runner(tmp_path, api)
        rec = {
            "benchmark_run_id": "run-x",
            "case_id": "live-aapl",
            "profile": "fast",
            "ticker": "AAPL",
            "repetition": 1,
            "technical_success": True,
        }
        runner.records.append(rec)
        runner._stop = True
        runner._save_partial()
        saved = json.loads((tmp_path / "jobs.jsonl").read_text("utf-8").splitlines()[0])
        assert saved["case_id"] == "live-aapl"
        assert (tmp_path / "summary_partial.json").exists()


# ---------------------------------------------------------------------------
# 脱敏 / report / claim eligibility
# ---------------------------------------------------------------------------


class TestSensitivityAndClaims:
    def test_redact(self) -> None:
        text = "key=sk-abcdef1234567890 token=Bearer abc.def.ghi api_key=secretvalue cookie=xyz"
        cleaned = lbm._redact(text)
        assert "sk-abcdef1234567890" not in cleaned
        assert "secretvalue" not in cleaned
        assert "abc.def.ghi" not in cleaned
        assert "REDACTED" in cleaned

    def test_placeholder_secret(self) -> None:
        assert lbm.is_placeholder_secret(None) is True
        assert lbm.is_placeholder_secret("") is True
        assert lbm.is_placeholder_secret("sk-123") is True
        assert lbm.is_placeholder_secret("your-api-key") is True
        assert lbm.is_placeholder_secret("sk-12345678901234567890") is False

    def test_load_records_do_not_enter_paired_claims(self) -> None:
        paired = {
            "suite": "paired",
            "token_usage_complete": True,
            "profile": "fast",
            "ticker": "A",
            "sector": "s",
            "case_id": "c",
            "repetition": 1,
            "pair_id": "p1",
            "live_acceptance_success": True,
            "technical_success": True,
            "full_quality_success": True,
            "official_sec_citation_count": 1,
            "e2e_duration_seconds": 10,
        }
        load_rec = dict(paired)
        load_rec["suite"] = "load"
        records = [paired, load_rec]
        eligibility = lbm._claim_eligibility(records, repeats=1)
        assert eligibility["paired_jobs"] == 1
        paired_rows = lbm._paired_comparison(records)
        assert len(paired_rows) == 0

    def test_paired_comparison(self) -> None:
        fast = {
            "suite": "paired",
            "pair_id": "p1",
            "profile": "fast",
            "ticker": "AAPL",
            "sector": "tech",
            "repetition": 1,
            "e2e_duration_seconds": 100.0,
            "execution_duration_seconds": 90.0,
            "total_tokens": 1000,
            "technical_success": True,
            "live_acceptance_success": True,
            "full_quality_success": True,
        }
        deep = dict(fast)
        deep.update(
            {
                "profile": "deep",
                "e2e_duration_seconds": 200.0,
                "execution_duration_seconds": 180.0,
                "total_tokens": 3000,
            }
        )
        rows = lbm._paired_comparison([fast, deep])
        assert len(rows) == 1
        assert rows[0]["e2e_pct_change_fast_vs_deep"] == 50.0
        assert rows[0]["token_pct_change"] == pytest.approx(66.6667, abs=0.01)

    def test_company_breakdown_excludes_load(self) -> None:
        paired = {
            "suite": "paired",
            "ticker": "AAPL",
            "sector": "tech",
            "e2e_duration_seconds": 10.0,
            "technical_success": True,
            "live_acceptance_success": True,
            "full_quality_success": True,
            "terminal_status": "succeeded",
        }
        load = dict(paired)
        load["suite"] = "load"
        rows = lbm._company_breakdown([paired, load])
        assert len(rows) == 1
        assert rows[0]["ticker"] == "AAPL"

    def test_report_contains_required_sections(self, tmp_path: Path) -> None:
        records = [
            {
                "suite": "paired",
                "pair_id": "p1",
                "profile": "fast",
                "ticker": "A",
                "sector": "s",
                "repetition": 1,
                "e2e_duration_seconds": 10.0,
                "execution_duration_seconds": 9.0,
                "total_tokens": 100,
                "technical_success": True,
                "live_acceptance_success": True,
                "full_quality_success": True,
                "official_sec_citation_count": 1,
            },
        ]
        summary = lbm._build_summary(
            "run-x", "paired", records, {"pricing_file": None}, {}, None, None
        )
        report = lbm._render_report(
            tmp_path,
            summary,
            records,
            lbm._paired_comparison(records),
            lbm._company_breakdown(records),
            {"git_commit": "abc"},
            {"claim_eligible": False},
        )
        assert "重要声明" in report
        assert "可以写进简历" in report
        assert "不能写进简历" in report
        assert "已知限制" in report
        assert "fake workflow benchmark" in report


# ---------------------------------------------------------------------------
# 真实并发（load）与跨并发级别 resume（反馈第 3/6 项）
# ---------------------------------------------------------------------------


class TestLoadConcurrencyAndResumeIdentity:
    """真实并发验证：注入带 sleep 的 fake run_one_fn，测量实际并发度 max_active。

    不能只检查 JobPlan.configured_concurrency 字段；必须证明 worker 真并发。
    """

    def _make_runner(
        self,
        tmp_path: Path,
        suite: str,
        plans: list[lbm.JobPlan],
        fake_run: Any,
        resume_existing: set[tuple[str, str, str, str, int, str, int]] | None = None,
    ) -> lbm.LiveBenchmarkRunner:
        return lbm.LiveBenchmarkRunner(
            suite=suite,
            plans=plans,
            api=FakeApi(),
            prom=FakeProm(),
            run_dir=tmp_path,
            timeout_s=60,
            max_failures=10,
            max_total_tokens=0,
            cooldown_s=0,
            resume_existing=resume_existing or set(),
            summary_config={"run_id": "run-x"},
            run_one_fn=fake_run,
        )

    def _concurrency_measure(self, max_workers: int, jobs: int) -> int:
        import threading

        active = 0
        max_active = 0
        lock = threading.Lock()
        barrier = threading.Barrier(max_workers) if max_workers > 1 else None

        def fake_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                # 用 barrier 强制所有 worker 同时进入临界区，暴露真实并发。
                if barrier is not None:
                    try:
                        barrier.wait(timeout=5)
                    except threading.BrokenBarrierError:
                        pass
                time.sleep(0.05)
            finally:
                with lock:
                    active -= 1
            return {"technical_success": True, "terminal_status": "succeeded"}

        plans = [make_plan(profile="fast", repetition=i + 1) for i in range(jobs)]
        # 统一改为 load suite + 指定 concurrency，触发 Runner 的线程池路径。
        for p in plans:
            p.suite = "load"
            p.configured_concurrency = max_workers
            p.pair_id = f"load-c{max_workers}-{p.repetition}"
        runner = self._make_runner(Path("."), "load", plans, fake_run)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lbm, "write_json", lambda *a, **k: None)
            mp.setattr(lbm, "write_jsonl", lambda *a, **k: None)
            runner.run()
        return max_active

    def test_load_concurrency_1_max_active_1(self) -> None:
        assert self._concurrency_measure(1, 4) == 1

    def test_load_concurrency_2_max_active_at_least_2(self) -> None:
        assert self._concurrency_measure(2, 4) >= 2

    def test_load_concurrency_4_max_active_at_least_4(self) -> None:
        assert self._concurrency_measure(4, 8) >= 4

    def test_load_resume_does_not_skip_across_levels(self, tmp_path: Path) -> None:
        """concurrency=1 已完成的任务不得让 concurrency=2/4 的同 case 任务被跳过。"""
        jobs = tmp_path / "jobs.jsonl"
        jobs.write_text(
            json.dumps(
                {
                    "terminal_status": "succeeded",
                    "suite": "load",
                    "case_id": "live-aapl",
                    "profile": "fast",
                    "ticker": "AAPL",
                    "repetition": 1,
                    "pair_id": "load-c1-1",
                    "configured_concurrency": 1,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        resume_set = lbm.read_existing_records(tmp_path)
        # concurrency=1 身份存在…
        assert ("load", "live-aapl", "fast", "AAPL", 1, "load-c1-1", 1) in resume_set
        # …但 concurrency=2/4 与之不同，不会被误跳过（pair_id 也不同）。
        assert ("load", "live-aapl", "fast", "AAPL", 1, "load-c2-1", 2) not in resume_set
        assert ("load", "live-aapl", "fast", "AAPL", 1, "load-c4-1", 4) not in resume_set

        # 构造 c1/c2/c4 三个 level 的 plan，只有 c1 已被终态跳过。
        executed: list[str] = []
        seen: list[str] = []

        def fake_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
            plan = kwargs["plan"]
            seen.append(plan.pair_id)
            executed.append(plan.pair_id)
            return {"technical_success": True, "terminal_status": "succeeded",
                    "suite": plan.suite, "case_id": plan.case_id, "profile": plan.profile,
                    "ticker": plan.ticker, "repetition": plan.repetition,
                    "pair_id": plan.pair_id, "configured_concurrency": plan.configured_concurrency}

        plans = []
        for level, pair in [(1, "load-c1-1"), (2, "load-c2-1"), (4, "load-c4-1")]:
            p = lbm.JobPlan(
                suite="load",
                case={"case_id": "live-aapl", "ticker": "AAPL", "sector": "tech",
                      "as_of_date": "2025-10-31", "language": "zh-CN",
                      "requested_forms": ["10-K", "10-Q"]},
                profile="fast", repetition=1, pair_id=pair,
                execution_order=level, configured_concurrency=level,
            )
            plans.append(p)
        runner = self._make_runner(tmp_path, "load", plans, fake_run, resume_existing=resume_set)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lbm, "write_json", lambda *a, **k: None)
            mp.setattr(lbm, "write_jsonl", lambda *a, **k: None)
            runner.run()
        assert "load-c1-1" not in executed  # c1 已被跳过
        assert "load-c2-1" in executed  # c2 不误跳过
        assert "load-c4-1" in executed  # c4 不误跳过


# ---------------------------------------------------------------------------
# 不联网 live happy-path（反馈第 7 项）
# ---------------------------------------------------------------------------


class TestLiveHappyPathOffline:
    """mock API/Prometheus/run_one_job，走真实 Settings/Load + --confirm-live。

    验证 settings_summary → LLMConfig.from_settings → collect_environment 不再被
    不存在的 import 阻塞，且整个 main() 在隔离栈中完成、不联网。
    """

    def _fake_settings(self) -> Any:
        from invest_research.settings import Settings

        return Settings(
            _env_file=None,
            flow_mode="live",
            llm_vendor="deepseek",
            llm_provider="openai_compatible",
            llm_api_key="sk-real-1234567890123456",
            llm_base_url="https://api.example.com/v1",
            llm_model_research="deepseek-r",
            llm_model_analysis="deepseek-a",
            llm_model_writer="deepseek-w",
            serper_api_key="real-serper-key-value",
            sec_user_agent_contact="test@example.com",
        )

    def test_main_live_happy_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = self._fake_settings()
        monkeypatch.setattr(lbm, "load_settings", lambda: settings)
        monkeypatch.setattr(lbm.time, "sleep", lambda *a, **k: None)

        class _FakeProm(lbm.PrometheusClient):
            def __init__(self, base_url: str, timeout_s: float = 15.0,
                         http: Optional[httpx.Client] = None) -> None:
                self.base_url = base_url.rstrip("/")
                self.timeout_s = timeout_s
                self.http = http or httpx.Client(timeout=3)
            def healthy(self) -> bool:
                return True
            def snapshot(self) -> dict[str, Any]:
                return {}
            def delta(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
                return {}
            def detect_counter_resets(self, delta: dict[str, Any]) -> list[str]:
                return []

        class _OkApi(lbm.ApiClient):
            def __init__(self, api_base: str, timeout_s: float = 30.0,
                         http: Optional[httpx.Client] = None) -> None:
                pass
            def health(self) -> bool:
                return True
            def readiness(self) -> tuple[bool, str]:
                return True, "ready"

        monkeypatch.setattr(lbm, "PrometheusClient", _FakeProm)
        monkeypatch.setattr(lbm, "ApiClient", _OkApi)
        # 注入 fake run_one_fn：避免真实 run_one_job 走 _OkApi 未实现的 create_job。
        def _fake_default(api: Any, *, plan: lbm.JobPlan, run_id: str, **kw: Any) -> dict[str, Any]:
            return {
                "terminal_status": "succeeded",
                "job_id": "00000000-0000-0000-0000-0000000000ab",
                "suite": plan.suite,
                "case_id": plan.case_id,
                "profile": plan.profile,
                "ticker": plan.ticker,
                "repetition": plan.repetition,
                "pair_id": plan.pair_id,
                "configured_concurrency": plan.configured_concurrency,
                "technical_success": True,
                "live_acceptance_success": True,
                "full_quality_success": True,
                "e2e_duration_seconds": 1.0,
            }

        monkeypatch.setattr(lbm, "_default_run_one", _fake_default)
        # 隔离输出目录，绝不写入真实 evals/live_runs。
        monkeypatch.setattr(lbm, "LIVE_RUNS_DIR", tmp_path / "live_runs")

        # 用极小参数：smoke + 1 job 覆盖 settings_summary/collect_environment/runner。
        args = [
            "--suite", "smoke", "--confirm-live",
            "--api-base", "http://fake.invalid/api",
            "--prometheus-base", "http://fake.invalid/prom",
            "--run-id", "happy",
        ]
        code = lbm.main(args)
        assert code == 0
        # 输出目录已生成完整产物（jobs.jsonl 至少含 4 条 smoke 记录）。
        out_dir = tmp_path / "live_runs" / "happy"
        assert (out_dir / "environment.json").exists()
        assert (out_dir / "summary.json").exists()
        records = (out_dir / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(records) >= 1
        # environment 不含密钥。
        env = json.loads((out_dir / "environment.json").read_text(encoding="utf-8"))
        assert "llm_api_key" not in json.dumps(env)
        assert "secret" not in json.dumps(env).lower()

    def test_settings_summary_uses_real_llm_config_api(self) -> None:
        """直接调用 settings_summary：确认走 LLMConfig.from_settings + config_for。"""
        from invest_research.settings import Settings

        # 构造真实 Settings 实例（绕过根 .env，全部用可构造值；
        # LLMConfig.from_settings 需要 build_role_llm_config，因此不能是 SimpleNamespace）。
        settings = Settings(
            _env_file=None,
            llm_api_key="sk-1234567890123456",
            llm_vendor="deepseek",
            llm_provider="openai_compatible",
            llm_base_url="https://api.example.com/v1",
            llm_model_research="deepseek-r",
            llm_model_analysis="deepseek-a",
            llm_model_writer="deepseek-w",
            llm_research_enable_thinking=False,
            llm_analysis_enable_thinking=True,
            llm_writer_enable_thinking=False,
            sec_user_agent_contact="test@example.com",
        )
        summary = lbm.settings_summary(settings)
        assert summary["flow_mode"] == settings.flow_mode
        models = summary["provider_model_profile"]
        # 三个角色都有 model/vendor 且不含密钥/base_url。
        for role in ("research", "analysis", "writer"):
            assert models[role]["model"]
            assert models[role]["vendor"] == "deepseek"
        assert models["research"]["enable_thinking"] is False
        assert models["analysis"]["enable_thinking"] is True
        assert models["writer"]["enable_thinking"] is False
        assert "api_key" not in json.dumps(summary).lower()
        assert "base_url" not in json.dumps(summary)



# ---------------------------------------------------------------------------
# P06-11 契约回归 + 免费 regrade（只读重评分，绝不 POST 新任务）
# ---------------------------------------------------------------------------


class TestContractRegression:
    """benchmark 契约必须与 ExecutionRecorder._ARTIFACT_TYPES / 真实步骤一致。"""

    def test_required_steps_match_real_contract(self) -> None:
        assert lbm.REQUIRED_STEPS == (
            "00_request",
            "01_company_resolve",
            "02_research",
            "03_documents",
            "04_analysis",
            "05_writer",
            "06_quality_gate",
            "07_manifest",
        )
        assert "01_company_profile" not in lbm.REQUIRED_STEPS

    def test_required_artifacts_match_real_contract(self) -> None:
        assert lbm.REQUIRED_ARTIFACT_KEYS == (
            "00_request.json",
            "02_research_pack.json",
            "04_financial_analysis_pack.json",
            "05_report_draft.json",
            "06_quality_report.json",
            "07_manifest.json",
            "08_report.md",
            "09_report.pdf",
        )
        assert "01_company_profile.json" not in lbm.REQUIRED_ARTIFACT_KEYS
        assert "03_documents.json" not in lbm.REQUIRED_ARTIFACT_KEYS

    def test_required_artifacts_match_execution_recorder(self) -> None:
        from invest_research.infrastructure.queue import execution_recorder as er

        recorder_keys = sorted(er._ARTIFACT_TYPES)
        benchmark_keys = sorted(lbm.REQUIRED_ARTIFACT_KEYS)
        assert benchmark_keys == recorder_keys


class TestFreeRegrade:
    """--regrade-existing 只读重评分：create_job 调用次数必须为 0。"""

    def test_regrade_readonly_and_expected_results(self, tmp_path: Path) -> None:
        # 构造一个已有 run（模拟旧任务的 jobs.jsonl + config.json）。
        run_dir = tmp_path / "run-x"
        run_dir.mkdir(parents=True)
        (run_dir / "config.json").write_text(
            json.dumps({"suite": "paired", "repeats": 1}), encoding="utf-8"
        )
        old_record = {
            "benchmark_run_id": "run-x",
            "suite": "paired",
            "case_id": "live-aapl",
            "ticker": "AAPL",
            "sector": "tech",
            "profile": "fast",
            "repetition": 1,
            "pair_id": "p1",
            "configured_concurrency": 1,
            "job_id": "00000000-0000-0000-0000-000000000001",
            "as_of_date": "2025-10-31",
            "terminal_status": "succeeded",
            "technical_success": False,  # 旧评测错误地判为 False
            "live_acceptance_success": False,
            "full_quality_success": False,
            "timeout": False,
        }
        (run_dir / "jobs.jsonl").write_text(
            json.dumps(old_record, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        # 旧任务 manifest：token_usage=null；quality=partial；包含 01_company_profile 步骤。
        research_pack = {
            "sources": [
                {
                    "canonical_url": "https://www.sec.gov/Archives/x.htm",
                    "locator": "Item 7",
                    "published_at": "2025-09-27",
                    "accessed_at": "2025-10-31",
                }
            ]
        }
        manifest = {
            "status": "published",
            "models": {"research": "m", "analysis": "m", "writer": "m"},
            "evidence": {
                "invocation_summary": {"sec_company_facts_calls": 1, "web_search_calls": 1}
            },
            "performance": {"token_usage": None},
        }
        artifacts = {
            "00_request.json": b"{}",
            "02_research_pack.json": json.dumps(research_pack).encode(),
            "04_financial_analysis_pack.json": json.dumps(
                {"completeness": "partial", "metrics": [], "facts": []}
            ).encode(),
            "05_report_draft.json": json.dumps(
                {"version": "v1", "title": "t", "markdown": "# r", "citation_keys": ["src_x"]}
            ).encode(),
            "06_quality_report.json": json.dumps(
                {
                    "version": "v1",
                    "all_passed": False,
                    "issues": [],
                    "warnings": [],
                    "recommendation": "publish_partial",
                }
            ).encode(),
            "07_manifest.json": json.dumps(manifest).encode(),
            "08_report.md": b"# report",
            "09_report.pdf": b"%PDF-1.4 fake",
        }

        class ReadOnlyApi:
            def __init__(self) -> None:
                self.create_calls = 0
                self.get_calls = 0

            def create_job(self, *a: Any, **k: Any) -> None:
                self.create_calls += 1
                raise AssertionError("regrade 期间禁止 POST 新任务")

            def get_job(self, job_id: str) -> tuple[int, dict[str, Any]]:
                self.get_calls += 1
                steps = [
                    {"step_name": n, "sequence_no": i, "status": "succeeded", "attempt_count": 1}
                    for i, n in enumerate(
                        (
                            "00_request",
                            "01_company_resolve",
                            "02_research",
                            "03_documents",
                            "04_analysis",
                            "05_writer",
                            "06_quality_gate",
                            "07_manifest",
                        ),
                        start=1,
                    )
                ]
                return 200, {
                    "job_id": job_id,
                    "status": "succeeded",
                    "current_step": None,
                    "error_code": None,
                    "failure_stage": None,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:05:00+00:00",
                    "steps": steps,
                }

            def list_artifacts(self, job_id: str) -> tuple[int, list[str]]:
                return 200, sorted(artifacts)

            def download_artifact(self, job_id: str, key: str) -> tuple[int, bytes]:
                return (200, artifacts[key]) if key in artifacts else (404, b"")

        api = ReadOnlyApi()
        code = lbm.regrade_existing("run-x", api, run_dir)
        assert code == 0
        # regrade 期间 create_job 调用次数必须是 0。
        assert api.create_calls == 0

        regraded = [json.loads(line) for line in
                    (run_dir / "jobs.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(regraded) == 1
        rec = regraded[0]
        # 修复后预期（partial 允许 live acceptance，不允许 full quality）。
        assert rec["technical_success"] is True
        assert rec["live_acceptance_success"] is True
        assert rec["full_quality_success"] is False
        # as-of 独立计算（旧任务来源日期不晚于 as_of → true）。
        assert rec["as_of_compliant"] is True
        # SEC 来源计数>0。
        assert rec["official_sec_source_count"] > 0
        # 无法精确映射（citation key 不是 URL sha256）→ coverage 为 null。
        assert rec["official_sec_citation_coverage"] is None
        # 旧任务 manifest.token_usage=null → 绝不补造。
        assert rec["token_usage_complete"] is False
        assert rec["total_tokens"] is None
        # 新记录字段。
        assert rec["evaluator_version"] == lbm.EVALUATOR_VERSION
        assert rec["regraded_at"]
        # 备份存在且幂等。
        assert (run_dir / "jobs.pre_regrade.jsonl").exists()
        second = lbm.regrade_existing("run-x", api, run_dir)
        assert second == 0

    def test_empty_record_persists_as_of_date(self) -> None:
        """_empty_record() 必须保存 plan.as_of_date。"""
        plan = make_plan()
        rec = lbm._empty_record("run-x", plan, "2026-01-01T00:00:00+00:00")
        assert rec["as_of_date"] == "2025-10-31"

    def test_regrade_does_not_require_confirm_live(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--regrade-existing 是免费只读：无需 --confirm-live 也能执行。"""
        called: list[str] = []

        class _FakeApi:
            def __init__(self, api_base: str, timeout_s: float = 30.0,
                         http: Optional[httpx.Client] = None) -> None:
                pass

        def _fake_regrade(run_id: str, api: Any, output_dir: Path) -> int:
            called.append(run_id)
            return 0

        # 隔离输出目录；regrade 不需要 Settings/Prometheus/create_job。
        monkeypatch.setattr(lbm, "LIVE_RUNS_DIR", tmp_path / "live_runs")
        monkeypatch.setattr(lbm, "ApiClient", _FakeApi)
        monkeypatch.setattr(lbm, "regrade_existing", _fake_regrade)
        code = lbm.main(["--regrade-existing", "run-legacy-x"])
        # 不提供 --confirm-live 也必须成功进入 regrade 分支。
        assert code == 0
        assert called == ["run-legacy-x"]

    def test_regrade_legacy_record_without_as_of_date(
        self, tmp_path: Path
    ) -> None:
        """真实 legacy fixture：jobs.jsonl 无 as_of_date，从 00_request.json 回退。

        期望 regrade 后：as_of_date=2025-10-31、as_of_compliant=true、
        technical=true、live_acceptance=true、full_quality=false、create_job=0。
        """
        run_dir = tmp_path / "run-legacy"
        run_dir.mkdir(parents=True)
        (run_dir / "config.json").write_text(
            json.dumps({"suite": "paired", "repeats": 1}), encoding="utf-8"
        )
        # 旧 jobs.jsonl 记录：明确没有 as_of_date 字段。
        legacy_row = {
            "benchmark_run_id": "run-legacy",
            "suite": "paired",
            "case_id": "live-msft",
            "ticker": "MSFT",
            "sector": "technology",
            "profile": "fast",
            "repetition": 1,
            "pair_id": "p1",
            "configured_concurrency": 1,
            "job_id": "00000000-0000-0000-0000-00000000legacy",
            "terminal_status": "succeeded",
            "technical_success": False,
            "live_acceptance_success": False,
            "full_quality_success": False,
            "evaluate_technical_failures": ["legacy technical failure"],
            "evaluate_live_acceptance_failures": ["legacy live failure"],
            "evaluate_full_quality_failures": ["legacy full-quality failure"],
            "timeout": False,
        }
        assert "as_of_date" not in legacy_row
        (run_dir / "jobs.jsonl").write_text(
            json.dumps(legacy_row, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        # 00_request.json 携带 as_of_date=2025-10-31；manifest 省略该字段以
        # 验证确实走 00_request 回退分支。
        research_pack = {
            "sources": [
                {
                    "canonical_url": "https://www.sec.gov/Archives/x.htm",
                    "locator": "Item 7",
                    "published_at": "2025-09-27",
                    "accessed_at": "2025-10-31",
                }
            ]
        }
        manifest = {
            "status": "published",
            "as_of_date": "2025-09-30",  # 与 00_request 不同，应优先取 00_request
            "models": {"research": "m", "analysis": "m", "writer": "m"},
            "evidence": {
                "invocation_summary": {"sec_company_facts_calls": 1, "web_search_calls": 1}
            },
            "performance": {"token_usage": None},
        }
        artifacts = {
            "00_request.json": json.dumps(
                {"input_company": "MSFT", "as_of_date": "2025-10-31",
                 "language": "zh-CN", "requested_forms": ["10-K", "10-Q"]}
            ).encode(),
            "02_research_pack.json": json.dumps(research_pack).encode(),
            "04_financial_analysis_pack.json": json.dumps(
                {"completeness": "partial", "metrics": [], "facts": []}
            ).encode(),
            "05_report_draft.json": json.dumps(
                {"version": "v1", "title": "t", "markdown": "# r", "citation_keys": ["src_x"]}
            ).encode(),
            "06_quality_report.json": json.dumps(
                {
                    "version": "v1",
                    "all_passed": False,
                    "issues": [],
                    "warnings": [],
                    "recommendation": "publish_partial",
                }
            ).encode(),
            "07_manifest.json": json.dumps(manifest).encode(),
            "08_report.md": b"# report",
            "09_report.pdf": b"%PDF-1.4 fake",
        }

        class ReadOnlyLegacyApi:
            def __init__(self) -> None:
                self.create_calls = 0
                self.get_calls = 0

            def create_job(self, *a: Any, **k: Any) -> None:
                self.create_calls += 1
                raise AssertionError("regrade 期间禁止 POST 新任务")

            def get_job(self, job_id: str) -> tuple[int, dict[str, Any]]:
                self.get_calls += 1
                steps = [
                    {"step_name": n, "sequence_no": i, "status": "succeeded", "attempt_count": 1}
                    for i, n in enumerate(
                        (
                            "00_request",
                            "01_company_resolve",
                            "02_research",
                            "03_documents",
                            "04_analysis",
                            "05_writer",
                            "06_quality_gate",
                            "07_manifest",
                        ),
                        start=1,
                    )
                ]
                return 200, {
                    "job_id": job_id,
                    "status": "succeeded",
                    "current_step": None,
                    "error_code": None,
                    "failure_stage": None,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:05:00+00:00",
                    "steps": steps,
                }

            def list_artifacts(self, job_id: str) -> tuple[int, list[str]]:
                return 200, sorted(artifacts)

            def download_artifact(self, job_id: str, key: str) -> tuple[int, bytes]:
                return (200, artifacts[key]) if key in artifacts else (404, b"")

        api = ReadOnlyLegacyApi()
        code = lbm.regrade_existing("run-legacy", api, run_dir)
        assert code == 0
        assert api.create_calls == 0  # 免费只读：create_job 调用次数=0

        regraded = [json.loads(line) for line in
                    (run_dir / "jobs.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(regraded) == 1
        rec = regraded[0]
        # as_of_date 从 00_request.json 回退（优先于 manifest）。
        assert rec["as_of_date"] == "2025-10-31"
        assert rec["as_of_compliant"] is True
        assert rec["technical_success"] is True
        assert rec["live_acceptance_success"] is True
        assert rec["full_quality_success"] is False
        assert "evaluate_technical_failures" not in rec
        assert "evaluate_live_acceptance_failures" not in rec
        assert rec["evaluate_full_quality_failures"] != ["legacy full-quality failure"]
        assert rec["evaluator_version"] == lbm.EVALUATOR_VERSION
        assert rec["regraded_at"]
        # 旧记录不含 as_of_date 的诊断：备份文件保留原样。
        backup = json.loads(
            (run_dir / "jobs.pre_regrade.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert "as_of_date" not in backup

    def test_regrade_legacy_record_falls_back_to_manifest(
        self, tmp_path: Path
    ) -> None:
        """当 00_request.json 也缺 as_of_date 时，回退到 07_manifest.json。"""
        run_dir = tmp_path / "run-legacy-manifest"
        run_dir.mkdir(parents=True)
        (run_dir / "config.json").write_text(
            json.dumps({"suite": "paired", "repeats": 1}), encoding="utf-8"
        )
        legacy_row = {
            "benchmark_run_id": "run-legacy-manifest",
            "suite": "paired",
            "case_id": "live-msft",
            "ticker": "MSFT",
            "sector": "technology",
            "profile": "fast",
            "repetition": 1,
            "pair_id": "p1",
            "configured_concurrency": 1,
            "job_id": "00000000-0000-0000-0000-00000000legacy",
            "terminal_status": "succeeded",
            "timeout": False,
        }
        (run_dir / "jobs.jsonl").write_text(
            json.dumps(legacy_row, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        research_pack = {
            "sources": [
                {
                    "canonical_url": "https://www.sec.gov/Archives/x.htm",
                    "locator": "Item 7",
                    "published_at": "2025-09-27",
                    "accessed_at": "2025-10-31",
                }
            ]
        }
        manifest = {
            "status": "published",
            "as_of_date": "2025-10-31",  # 只有 manifest 提供日期
            "models": {"research": "m", "analysis": "m", "writer": "m"},
            "evidence": {
                "invocation_summary": {"sec_company_facts_calls": 1, "web_search_calls": 1}
            },
            "performance": {"token_usage": None},
        }
        artifacts = {
            "00_request.json": json.dumps(
                {"input_company": "MSFT", "language": "zh-CN",
                 "requested_forms": ["10-K", "10-Q"]}
            ).encode(),  # 无 as_of_date
            "02_research_pack.json": json.dumps(research_pack).encode(),
            "04_financial_analysis_pack.json": json.dumps(
                {"completeness": "partial", "metrics": [], "facts": []}
            ).encode(),
            "05_report_draft.json": json.dumps(
                {"version": "v1", "title": "t", "markdown": "# r", "citation_keys": ["src_x"]}
            ).encode(),
            "06_quality_report.json": json.dumps(
                {
                    "version": "v1",
                    "all_passed": False,
                    "issues": [],
                    "warnings": [],
                    "recommendation": "publish_partial",
                }
            ).encode(),
            "07_manifest.json": json.dumps(manifest).encode(),
            "08_report.md": b"# report",
            "09_report.pdf": b"%PDF-1.4 fake",
        }

        class ReadOnlyManifestApi:
            def __init__(self) -> None:
                self.create_calls = 0

            def create_job(self, *a: Any, **k: Any) -> None:
                self.create_calls += 1
                raise AssertionError("regrade 期间禁止 POST 新任务")

            def get_job(self, job_id: str) -> tuple[int, dict[str, Any]]:
                steps = [
                    {"step_name": n, "sequence_no": i, "status": "succeeded", "attempt_count": 1}
                    for i, n in enumerate(
                        (
                            "00_request",
                            "01_company_resolve",
                            "02_research",
                            "03_documents",
                            "04_analysis",
                            "05_writer",
                            "06_quality_gate",
                            "07_manifest",
                        ),
                        start=1,
                    )
                ]
                return 200, {
                    "job_id": job_id,
                    "status": "succeeded",
                    "current_step": None,
                    "error_code": None,
                    "failure_stage": None,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:05:00+00:00",
                    "steps": steps,
                }

            def list_artifacts(self, job_id: str) -> tuple[int, list[str]]:
                return 200, sorted(artifacts)

            def download_artifact(self, job_id: str, key: str) -> tuple[int, bytes]:
                return (200, artifacts[key]) if key in artifacts else (404, b"")

        api = ReadOnlyManifestApi()
        assert lbm.regrade_existing("run-legacy-manifest", api, run_dir) == 0
        assert api.create_calls == 0
        rec = json.loads(
            (run_dir / "jobs.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert rec["as_of_date"] == "2025-10-31"
        assert rec["as_of_compliant"] is True
        assert rec["technical_success"] is True
        assert rec["live_acceptance_success"] is True
        assert rec["full_quality_success"] is False

    def test_regrade_legacy_record_missing_all_as_of_dates(
        self, tmp_path: Path
    ) -> None:
        """三处都取不到 as_of_date 时写 None（不伪造）。"""
        row = {
            "suite": "paired",
            "case_id": "live-aapl",
            "profile": "fast",
            "ticker": "AAPL",
            "repetition": 1,
            "terminal_status": "succeeded",
            "timeout": False,
        }
        job = {
            "status": "succeeded",
            "current_step": None,
            "steps": [
                {"step_name": n, "status": "succeeded"}
                for n in (
                    "00_request", "01_company_resolve", "02_research", "03_documents",
                    "04_analysis", "05_writer", "06_quality_gate", "07_manifest",
                )
            ],
        }
        artifacts = make_artifacts()
        # 全部去掉 as_of_date 来源：row 无、00_request 无、manifest 无。
        artifacts["00_request.json"] = b"{}"
        manifest = json.loads(artifacts["07_manifest.json"])
        manifest.pop("as_of_date", None)
        artifacts["07_manifest.json"] = json.dumps(manifest).encode()
        rec = lbm._regrade_record(row, job, artifacts)
        assert rec["as_of_date"] is None
        # 无 as_of 时 as_of_compliance 判 None（research_pack 有发布日但 as_of 空 → None）。
        assert rec["as_of_compliant"] is None

    def test_regrade_cli_entrypoint_defines_function_before_main(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(lbm.REPO_ROOT / "scripts" / "run_live_portfolio_benchmark.py"),
                "--regrade-existing",
                "definitely-missing-run",
            ],
            cwd=lbm.REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode == 7
        assert "不存在 jobs.jsonl" in result.stdout
        assert "NameError" not in result.stderr
