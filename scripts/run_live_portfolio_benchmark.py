"""P06-11: live portfolio benchmark (fast/deep efficiency comparison, opt-in).

本脚本实现"10 家公司 fast/deep 真实效率对照实验"的自动化评测工具。它只负责
评测、数据采集、报告与必要的无侵入性读取，**不修改业务流程、Prompt、Agent
行为或质量门禁**。

安全边界（与 run_phase6_benchmark.py 相同思路，但专门针对真实 live）:
- 默认是 dry-run：不联网、不调用 SEC / Serper / LLM、不写任何运行目录。
- 真正运行必须同时满足:
  1. ``--confirm-live`` 显式提供;
  2. 从根目录 .env 读取的 ``FLOW_MODE==live``（Settings 校验）;
  3. API / readiness / Prometheus 预检通过;
  4. LLM/Serper 密钥非占位符（拒绝 fake/placeholder/secret/sk- 前缀占位）。
- 不读取/打印/写入任何 API Key、完整 Prompt、完整模型响应或推理内容；日志、
  异常、JSONL、报告中一律脱敏。
- 不执行 docker compose down -v；不删除现有 volume/数据库/工件/evals/runs。

评测套件:
- smoke  : 2 家公司 x (fast+deep) = 4 个 job；concurrency=1；验证配置与量级。
- paired : 10 家公司 x fast/deep x repeats；concurrency 固定 1；固定 seed；按
  公司+repetition 配对；fast-first/deep-first 顺序平衡；结果原始+配对双口径。
- load   : 并发容量实验（与 paired 分离）；--concurrency-levels 1,2,4；按 level
  分组并使用有界 ThreadPoolExecutor（max_workers=1/2/4）真实并发；默认 fast
  省钱；只测排队/真实并发能力并明确标注。

成功口径（三个层次，绝不混用）:
- technical_success     : API succeeded + 工件齐 + PDF 合法 + 无泄漏...
- live_acceptance       : technical + 真实 SEC/Serper/LLM 证据 + 质量门禁
                          published/publish_partial + citation + as_of 合规...
- full_quality_pass     : live_acceptance + all_passed=True + published +
                          analysis_completeness=complete。

本文件不做任何真实付费调用；真实 live 运行必须由用户在确认环境后显式触发。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
EVALS_DIR = REPO_ROOT / "evals"
LIVE_RUNS_DIR = EVALS_DIR / "live_runs"
ANNUAL_BENCHMARK_RUNS_DIR = EVALS_DIR / "annual_benchmark_runs"
DEFAULT_DATASET = EVALS_DIR / "live_dataset.json"
DEFAULT_ANNUAL_DATASET = EVALS_DIR / "annual_benchmark_dataset.json"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

# 评测器版本（每次修复评测口径时递增；regrade 会把本版本写入每条记录）。
EVALUATOR_VERSION = "v4"

KIB = 1024
DEFAULT_TIMEOUT_S = 600.0
DEFAULT_MAX_FAILURES = 2
DEFAULT_MAX_TOTAL_TOKENS = 20_000_000  # best-effort 熔断，仅基于真实 usage

PROFILES = ("fast", "deep")
TERMINAL_JOB_STATES = {"succeeded", "partial", "failed", "cancelled"}
ACCEPTED_RECOMMENDATIONS = {"published", "publish_partial"}
# 与 ExecutionRecorder._ARTIFACT_TYPES 的真实注册契约保持一致：
# 系统设计不存在 01_company_profile.json / 03_documents.json 工件。
REQUIRED_ARTIFACT_KEYS = (
    "00_request.json",
    "02_research_pack.json",
    "04_financial_analysis_pack.json",
    "05_report_draft.json",
    "06_quality_report.json",
    "07_manifest.json",
    "08_report.md",
    "09_report.pdf",
)
# 真实步骤契约（与 ExecutionRecorder._STEP_SPECS / flow_wiring 一致）：
# 01 是 company_resolve（不是 company_profile）。
REQUIRED_STEPS = (
    "00_request",
    "01_company_resolve",
    "02_research",
    "03_documents",
    "04_analysis",
    "05_writer",
    "06_quality_gate",
    "07_manifest",
)
QUALITY_ARTIFACT = "06_quality_report.json"
MANIFEST_ARTIFACT = "07_manifest.json"
RESEARCH_PACK_ARTIFACT = "02_research_pack.json"
REPORT_DRAFT_ARTIFACT = "05_report_draft.json"
ANALYSIS_PACK_ARTIFACT = "04_financial_analysis_pack.json"
REPORT_MD_ARTIFACT = "08_report.md"
REPORT_PDF_ARTIFACT = "09_report.pdf"
ANNUAL_RUNTIME_ARTIFACT = "annual/runtime_state.json"
ANNUAL_FACTS_ARTIFACT = "annual/company-facts/selected.json"
ANNUAL_FACTS_MANIFEST_ARTIFACT = "annual/company-facts/manifest.json"
ANNUAL_COMPARISON_ARTIFACT = "annual/comparison.json"
ANNUAL_SECTION_ARTIFACTS = (
    "annual/sections/financial_performance.json",
    "annual/sections/business_overview.json",
    "annual/sections/risk_factors.json",
    "annual/sections/material_events.json",
)
ANNUAL_REQUIRED_ARTIFACT_KEYS = (
    "00_request.json",
    REPORT_DRAFT_ARTIFACT,
    QUALITY_ARTIFACT,
    MANIFEST_ARTIFACT,
    REPORT_MD_ARTIFACT,
    REPORT_PDF_ARTIFACT,
    ANNUAL_RUNTIME_ARTIFACT,
    ANNUAL_FACTS_ARTIFACT,
    ANNUAL_FACTS_MANIFEST_ARTIFACT,
    ANNUAL_COMPARISON_ARTIFACT,
    *ANNUAL_SECTION_ARTIFACTS,
)

SEC_OFFICIAL_HOSTS = ("sec.gov", "data.sec.gov", "www.sec.gov")

PROMETHEUS_METRICS = (
    "llm_requests_total",
    "llm_tokens_total",
    "llm_usage_missing_total",
    "llm_request_duration_seconds",
    "agent_runs_total",
    "agent_duration_seconds",
    "tool_duration_seconds",
    "stage_duration_seconds",
    "quality_gate_failures_total",
    "analysis_completeness_total",
    "revision_total",
    "tool_cache_total",
    "research_job_duration_seconds",
    "tool_calls_total",
    "tool_retries_total",
    "research_jobs_total",
    "annual_node_transitions_total",
    "annual_node_duration_seconds",
    "annual_nodes_waiting",
)

_PLACEHOLDER_MARKERS = ("your-", "xxx", "placeholder", "<your", "changeme", "secret", "example")


# ---------------------------------------------------------------------------
# 纯工具函数（可离线单测）
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_short(text: str, length: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _safe_name(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "-_.")
    return cleaned or uuid.uuid4().hex[:12]


def _redact(value: str) -> str:
    """脱敏：密钥 / Bearer / sk- / 疑似占位密钥。绝不打印 key 明文。"""
    if not value:
        return ""
    lowered = value
    lowered = re.sub(r"(?i)sk-[a-z0-9_-]{6,}", "sk-***REDACTED***", lowered)
    lowered = re.sub(r"(?i)Bearer\s+[a-zA-Z0-9._~+/=-]{6,}", "Bearer ***REDACTED***", lowered)
    lowered = re.sub(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*)[^,;\s}]{6,}", r"\1***REDACTED***", lowered)
    lowered = re.sub(
        r"(?i)(authorization[\"']?\s*[:=]\s*)[^,;\s}]{6,}", r"\1***REDACTED***", lowered
    )
    lowered = re.sub(r"(?i)(cookie[\"']?\s*[:=]\s*)[^,;\s}]{6,}", r"\1***REDACTED***", lowered)
    return lowered


def _bounded_ascii(text: str, limit: int = 200) -> str:
    if not text:
        return ""
    return " ".join(text.split())[:limit]


def is_placeholder_secret(value: str | None) -> bool:
    """占位符密钥判定（与 settings.is_placeholder_secret 同思路，不引入依赖）。"""
    if not value:
        return True
    lowered = value.strip().lower()
    if not lowered:
        return True
    if lowered.startswith("sk-") and len(lowered) < 12:
        return True  # 过短的 sk- 视为占位（真实 key 通常更长）
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _percentile(values: Sequence[float], q: float) -> float | None:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    if q <= 0.0:
        return vals[0]
    if q >= 1.0:
        return vals[-1]
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] + (vals[hi] - vals[lo]) * frac


def aggregate_durations(records: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [float(r[field]) for r in records if r.get(field) is not None and r[field] != ""]
    if not values:
        return {
            "n": 0,
            "avg": None,
            "min": None,
            "max": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
        }
    return {
        "n": len(values),
        "avg": round(sum(values) / len(values), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "p50": round(_percentile(values, 0.50) or 0.0, 6),
        "p90": round(_percentile(values, 0.90) or 0.0, 6),
        "p95": round(_percentile(values, 0.95) or 0.0, 6),
        "p99": round(_percentile(values, 0.99) or 0.0, 6),
    }


def wilson_interval(success: int, total: int, z: float = 1.96) -> dict[str, float | None]:
    """Wilson 95% 置信区间；total<=0 返回 None 边界。"""
    if total <= 0:
        return {"lo": None, "hi": None}
    if success == 0:
        return {"lo": 0.0, "hi": 0.0}
    if success == total:
        return {"lo": 1.0, "hi": 1.0}
    p = success / total
    z2 = z * z
    denom = 1 + z2 / total
    centre = p + z2 / (2 * total)
    margin = z * ((p * (1 - p) + z2 / (4 * total)) / total) ** 0.5
    return {
        "lo": round(max(0.0, (centre - margin) / denom), 4),
        "hi": round(min(1.0, (centre + margin) / denom), 4),
    }


def rate_stats(records: Sequence[dict[str, Any]], key: str) -> dict[str, Any]:
    total = len(records)
    ok = sum(1 for r in records if r.get(key) is True)
    interval = wilson_interval(ok, total)
    return {
        "numerator": ok,
        "denominator": total,
        "rate": round(ok / total, 4) if total else None,
        "wilson_low": interval["lo"],
        "wilson_high": interval["hi"],
    }


def fidelity_rate(records: Sequence[dict[str, Any]], key: str) -> float | None:
    """v4：已计算字段（非 None）中为 True 的比例；无已计算记录返回 None。"""
    considered = [r for r in records if r.get(key) is not None]
    if not considered:
        return None
    return round(sum(1 for r in considered if r.get(key) is True) / len(considered), 4)


def pct_change(deep_value: float | None, fast_value: float | None) -> float | None:
    """fast 相对 deep 的变化：(deep - fast) / deep * 100。deep=0/None 时返回 None。"""
    if deep_value is None or fast_value is None:
        return None
    if deep_value == 0:
        return None
    return round((deep_value - fast_value) / deep_value * 100.0, 4)


# ---------------------------------------------------------------------------
# 数据集
# ---------------------------------------------------------------------------


def load_live_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"数据集不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "live_dataset_v1":
        raise ValueError(f"不支持的 dataset schema: {data.get('schema_version')!r}")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("数据集 cases 必须是非空列表")
    return list(cases)


def validate_dataset(cases: list[dict[str, Any]], *, annual_only: bool = False) -> list[str]:
    """返回数据集校验错误列表（空表示通过）。"""
    errors: list[str] = []
    seen_tickers: set[str] = set()
    for i, case in enumerate(cases):
        prefix = f"case[{i}]"
        case_id = case.get("case_id")
        if not case_id or not isinstance(case_id, str):
            errors.append(f"{prefix}: case_id 缺失或非字符串")
        ticker = case.get("ticker")
        if not ticker or not isinstance(ticker, str):
            errors.append(f"{prefix}: ticker 缺失或非字符串")
        elif ticker in seen_tickers:
            errors.append(f"{prefix}: ticker {ticker!r} 重复")
        else:
            seen_tickers.add(ticker)
        sector = case.get("sector")
        if not sector or not isinstance(sector, str):
            errors.append(f"{prefix}: sector 缺失或非字符串")
        as_of = case.get("as_of_date")
        try:
            date.fromisoformat(str(as_of))
        except (TypeError, ValueError):
            errors.append(f"{prefix}: as_of_date 非法 ISO 日期: {as_of!r}")
        if case.get("language") != "zh-CN":
            errors.append(f"{prefix}: language 必须是 zh-CN（当前 {case.get('language')!r}）")
        forms = case.get("requested_forms")
        if annual_only:
            if forms != ["10-K"]:
                errors.append(f"{prefix}: annual-paired 的 requested_forms 必须恰为 [10-K]")
        elif not isinstance(forms, list) or "10-K" not in forms or "10-Q" not in forms:
            errors.append(f"{prefix}: requested_forms 必须同时包含 10-K 和 10-Q")
    return errors


# ---------------------------------------------------------------------------
# 任务计划（suite -> ordered jobs）
# ---------------------------------------------------------------------------


class JobPlan:
    """一个待执行任务。所有字段稳定，用于生成幂等键与原始记录。"""

    def __init__(
        self,
        *,
        suite: str,
        case: dict[str, Any],
        profile: str,
        research_mode: str = "legacy",
        repetition: int,
        pair_id: str | None = None,
        execution_order: int = 0,
        configured_concurrency: int = 1,
    ) -> None:
        self.suite = suite
        self.case = dict(case)
        self.profile = profile
        self.research_mode = research_mode
        self.repetition = repetition
        self.pair_id = pair_id
        self.execution_order = execution_order
        self.configured_concurrency = configured_concurrency

    @property
    def case_id(self) -> str:
        return str(self.case["case_id"])

    @property
    def ticker(self) -> str:
        return str(self.case["ticker"])

    @property
    def sector(self) -> str:
        return str(self.case.get("sector", ""))

    @property
    def as_of_date(self) -> str:
        return str(self.case["as_of_date"])

    def identity(self) -> tuple[Any, ...]:
        """run_id 无关的稳定身份（含 suite/pair_id/configured_concurrency）。

        对 load suite 而言，case=ticker/profile/repetition 相同但 concurrency level
        不同或 pair_id 不同（带序号）的任务是不同的实例；缺少这两项会把
        concurrency=1/2/4 的任务误判为同一任务而错误跳过。
        """
        legacy_identity: tuple[Any, ...] = (
            self.suite,
            self.case_id,
            self.profile,
            self.ticker,
            self.repetition,
            str(self.pair_id or ""),
            self.configured_concurrency,
        )
        # 兼容 P06 的历史恢复键；只有 annual-paired 显式加入模式，避免两个模式互相跳过。
        return (
            (*legacy_identity[:3], self.research_mode, *legacy_identity[3:])
            if self.research_mode != "legacy"
            else legacy_identity
        )


def build_smoke_plan(cases: list[dict[str, Any]], seed: int) -> list[JobPlan]:
    """smoke：前 2 家公司 x fast/deep，concurrency=1，repeats=1，顺序平衡。"""
    if len(cases) < 2:
        raise ValueError("smoke 需要至少 2 家公司")
    selected = list(cases[:2])
    rng = random.Random(seed)
    order: list[tuple[str, str]] = []
    for case in selected:
        profs = ["fast", "deep"]
        if rng.random() < 0.5:
            profs = ["deep", "fast"]
        for p in profs:
            order.append((str(case["case_id"]), p))
    plans: list[JobPlan] = []
    for idx, (case_id, prof) in enumerate(order, start=1):
        case = next(c for c in selected if c["case_id"] == case_id)
        plans.append(
            JobPlan(
                suite="smoke",
                case=case,
                profile=prof,
                repetition=1,
                pair_id=None,
                execution_order=idx,
                configured_concurrency=1,
            )
        )
    return plans


def build_paired_plan(cases: list[dict[str, Any]], repeats: int, seed: int) -> list[JobPlan]:
    """paired：10 公司 x 2 profile x repeats；concurrency=1；fast/deep 顺序平衡。

    平衡规则：对每一家公司，第一 repetition 按公司索引奇偶决定 fast/deep 谁先
    （一半公司 fast 先、一半 deep 先）；下一 repetition 交换顺序。同公司同一
    repetition 的 fast+deep 构成一个 pair（pair_id = live-<ticker>-r<rep>）。
    """
    if repeats < 1:
        raise ValueError("paired repeats 必须 >= 1")
    rng = random.Random(seed)
    # 固定公司顺序（不洗牌，保证可复现）。
    ordered = list(cases)
    plans: list[JobPlan] = []
    idx = 0
    for rep in range(1, repeats + 1):
        for case_index, case in enumerate(ordered):
            first = "fast" if (case_index + rep) % 2 == 0 else "deep"
            second = "deep" if first == "fast" else "fast"
            for prof in (first, second):
                idx += 1
                plans.append(
                    JobPlan(
                        suite="paired",
                        case=case,
                        profile=prof,
                        repetition=rep,
                        pair_id=f"live-{case['ticker']}-r{rep}",
                        execution_order=idx,
                        configured_concurrency=1,
                    )
                )
            _ = rng  # seed 只保留给可复现；顺序已确定性平衡
    return plans


def build_annual_paired_plan(
    cases: list[dict[str, Any]], repeats: int, seed: int
) -> list[JobPlan]:
    """annual-paired：同公司同轮次配对 legacy/annual_deep，固定 deep 且串行。"""
    if repeats < 1:
        raise ValueError("annual-paired repeats 必须 >= 1")
    plans: list[JobPlan] = []
    idx = 0
    for rep in range(1, repeats + 1):
        for case_index, case in enumerate(cases):
            # 每个 pair 交替首发模式，降低缓存、限流或预热对一方的系统性偏置。
            modes = (
                ("legacy", "annual_deep")
                if (case_index + rep) % 2 == 0
                else ("annual_deep", "legacy")
            )
            for mode in modes:
                idx += 1
                plans.append(
                    JobPlan(
                        suite="annual-paired",
                        case=case,
                        profile="deep",
                        research_mode=mode,
                        repetition=rep,
                        pair_id=f"annual-{case['ticker']}-r{rep}",
                        execution_order=idx,
                        configured_concurrency=1,
                    )
                )
    _ = seed  # 参数仍写入 config，后续扩样时保持 CLI 契约稳定。
    return plans


def build_load_plan(
    cases: list[dict[str, Any]],
    concurrency_levels: Sequence[int],
    jobs_per_level: int,
    profile: str,
    seed: int,
) -> list[JobPlan]:
    """load：每个 concurrency 级别各 jobs_per_level 个任务（默认 fast）。"""
    if profile not in PROFILES:
        raise ValueError(f"load profile 必须是 fast/deep，得到 {profile!r}")
    if jobs_per_level < 1:
        raise ValueError("jobs-per-level 必须 >= 1")
    rng = random.Random(seed)
    pool = list(cases)
    plans: list[JobPlan] = []
    idx = 0
    for level in concurrency_levels:
        if level not in (1, 2, 4):
            raise ValueError("concurrency-levels 只支持 1,2,4（不扩大并发）")
        # 用固定顺序轮转公司，保证可复现。
        for j in range(jobs_per_level):
            case = pool[j % len(pool)]
            idx += 1
            plans.append(
                JobPlan(
                    suite="load",
                    case=case,
                    profile=profile,
                    repetition=1,
                    pair_id=f"load-c{level}-{j + 1}",
                    execution_order=idx,
                    configured_concurrency=level,
                )
            )
        _ = rng
    return plans


def build_plan(suite: str, cases: list[dict[str, Any]], args: argparse.Namespace) -> list[JobPlan]:
    if suite == "smoke":
        return build_smoke_plan(cases, args.seed)
    if suite == "paired":
        return build_paired_plan(cases, args.repeats, args.seed)
    if suite == "annual-paired":
        return build_annual_paired_plan(cases, args.repeats, args.seed)
    if suite == "load":
        return build_load_plan(
            cases, args.concurrency_levels, args.jobs_per_level, args.profile, args.seed
        )
    raise ValueError(f"未知 suite: {suite}")


def job_payload(plan: JobPlan) -> dict[str, Any]:
    """POST /v1/research-jobs 请求体（ResearchRequest 字段）。"""
    payload = {
        "input_company": plan.ticker,
        "as_of_date": plan.as_of_date,
        "language": plan.case.get("language", "zh-CN"),
        "requested_forms": list(plan.case.get("requested_forms", ["10-K", "10-Q"])),
        "research_profile": plan.profile,
    }
    if plan.research_mode != "legacy":
        payload["research_mode"] = plan.research_mode
    return payload


def idempotency_key(run_id: str, plan: JobPlan) -> str:
    """稳定幂等键：包含 run_id/suite/case/profile/repetition/pair_id/concurrency。"""
    return (
        f"livebench-{run_id}-{plan.suite}-{plan.case_id}-{plan.profile}-{plan.research_mode}"
        f"-r{plan.repetition}-p{plan.pair_id or 'np'}-c{plan.configured_concurrency}"
    )


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------


class ApiClient:
    """封装评测所需的只读 + 创建 API（全部显式超时）。"""

    def __init__(
        self, api_base: str, timeout_s: float = 30.0, http: Optional[httpx.Client] = None
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout_s = timeout_s
        self.http = http or httpx.Client(timeout=httpx.Timeout(timeout_s))

    def health(self) -> bool:
        try:
            r = self.http.get(f"{self.api_base}/health", timeout=min(self.timeout_s, 10))
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def readiness(self) -> tuple[bool, str]:
        try:
            r = self.http.get(f"{self.api_base}/readiness", timeout=min(self.timeout_s, 10))
            if r.status_code == 200:
                return True, "ready"
            return False, r.text[:80]
        except httpx.HTTPError as exc:
            return False, _bounded_ascii(_redact(str(exc)))

    def create_job(self, payload: dict[str, Any], key: str) -> tuple[int, dict[str, Any]]:
        headers = {"Idempotency-Key": key, "Content-Type": "application/json"}
        try:
            resp = self.http.post(
                f"{self.api_base}/v1/research-jobs", json=payload, headers=headers
            )
            if resp.status_code == 409:
                # 幂等冲突：无法从 409 恢复 job_id（契约限制，见 report 限制节）。
                return resp.status_code, {"detail": "idempotency_conflict"}
            if resp.status_code >= 400:
                return resp.status_code, {"detail": _bounded_ascii(_redact(resp.text))}
            return resp.status_code, resp.json()
        except httpx.HTTPError as exc:
            return 0, {"error": _bounded_ascii(_redact(str(exc)))}

    def get_job(self, job_id: str) -> tuple[int, dict[str, Any]]:
        try:
            resp = self.http.get(f"{self.api_base}/v1/research-jobs/{job_id}")
            return resp.status_code, resp.json()
        except httpx.HTTPError as exc:
            return 0, {"error": _bounded_ascii(_redact(str(exc)))}

    def list_artifacts(self, job_id: str) -> tuple[int, list[str]]:
        try:
            resp = self.http.get(f"{self.api_base}/v1/research-jobs/{job_id}/artifacts")
            if resp.status_code != 200:
                return resp.status_code, []
            names = [str(a.get("artifact_key", "")) for a in resp.json() if isinstance(a, dict)]
            return resp.status_code, names
        except httpx.HTTPError:
            return 0, []

    def download_artifact(self, job_id: str, artifact_key: str) -> tuple[int, bytes]:
        try:
            resp = self.http.get(
                f"{self.api_base}/v1/research-jobs/{job_id}/artifacts/{artifact_key}"
            )
            return resp.status_code, resp.content
        except httpx.HTTPError:
            return 0, b""


class PrometheusClient:
    """Prometheus 快照：保留 label 维度（不只存 sum）；before/after delta。"""

    METRICS: tuple[str, ...] = PROMETHEUS_METRICS

    def __init__(
        self, base_url: str, timeout_s: float = 15.0, http: Optional[httpx.Client] = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.http = http or httpx.Client(timeout=httpx.Timeout(timeout_s))

    def healthy(self) -> bool:
        try:
            r = self.http.get(f"{self.base_url}/-/healthy", timeout=min(self.timeout_s, 10))
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def query(self, metric: str) -> list[dict[str, Any]]:
        resp = self.http.get(
            f"{self.base_url}/api/v1/query",
            params={"query": metric},
            timeout=self.timeout_s + 5,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus 查询失败: metric={metric} status={data.get('status')}")
        rows = data.get("data", {}).get("result", [])
        out: list[dict[str, Any]] = []
        for row in rows:
            metric = row.get("metric") or {}
            value = row.get("value")
            out.append({"labels": dict(metric), "value": value})
        return out

    def snapshot(self) -> dict[str, Any]:
        """任一指标查询失败 -> 抛异常（fail-fast，禁止空 dict 伪装成功）。"""
        out: dict[str, Any] = {}
        for name in self.METRICS:
            try:
                out[name] = self.query(name)
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Prometheus 不可达/查询失败: {name}: {_redact(str(exc))}"
                ) from exc
            time.sleep(0.05)
        return out

    @staticmethod
    def _label_key(labels: dict[str, Any]) -> str:
        return "|".join(f"{k}={v}" for k, v in sorted(labels.items()))

    @staticmethod
    def _labels_from_key(key: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for part in key.split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k] = v
        return out

    @staticmethod
    def _row_value(row: dict[str, Any]) -> float:
        value = row.get("value")
        if not value or len(value) < 2:
            return 0.0
        try:
            return float(value[1])
        except (TypeError, ValueError):
            return 0.0

    def delta(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in before:
            after_rows = {
                self._label_key(r["labels"]): self._row_value(r) for r in after.get(name, [])
            }
            before_rows = {
                self._label_key(r["labels"]): self._row_value(r) for r in before.get(name, [])
            }
            merged = set(before_rows) | set(after_rows)
            dims: dict[str, Any] = {}
            for key in merged:
                b = before_rows.get(key, 0.0)
                a = after_rows.get(key, 0.0)
                de = a - b
                if de or a or b:
                    dims[key] = {
                        "labels": self._labels_from_key(key),
                        "before": b,
                        "after": a,
                        "delta": de,
                    }
            out[name] = dims
        return out

    def detect_counter_resets(self, delta: dict[str, Any]) -> list[str]:
        """检测 Counter 重置（after < before 的 Counter 维度应告警，不算本轮结果）。"""
        resets: list[str] = []
        for name, dims in delta.items():
            for key, entry in dims.items():
                if entry["before"] > 0 and entry["delta"] < 0:
                    resets.append(
                        f"{name}{{{key}}}: before={entry['before']} after={entry['after']}"
                    )
        return resets


# ---------------------------------------------------------------------------
# 工件解析与三层成功判定
# ---------------------------------------------------------------------------


def _json_bytes(data: bytes) -> dict[str, Any] | None:
    if not data:
        return None
    try:
        parsed = json.loads(data.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _count_citations(report_draft: dict[str, Any] | None) -> dict[str, Any]:
    keys = (report_draft or {}).get("citation_keys") or []
    valid = [
        k for k in keys if isinstance(k, str) and k.startswith(("src_", "fr_", "claim-", "ref_"))
    ]
    return {"citation_count": len(keys), "valid_citation_count": len(valid)}


def _official_sec_source_count(research_pack: dict[str, Any] | None) -> int:
    """统计带 locator 的官方 SEC 来源数量（独立于 citation 映射）。"""
    if not research_pack:
        return 0
    count = 0
    for src in research_pack.get("sources") or []:
        if not isinstance(src, dict):
            continue
        url = str(src.get("canonical_url") or "")
        locator = str(src.get("locator") or "").strip()
        if any(host in url for host in SEC_OFFICIAL_HOSTS) and locator:
            count += 1
    return count


def _source_citation_key(url: str) -> str:
    """与 CitationRegistry / ReportDraftAssembler 同一算法：src_ + URL sha256 前 12 位。"""
    digest = hashlib.sha256(str(url or "").encode("utf-8")).hexdigest()
    return f"src_{digest[:12]}"


def _map_citations_to_sec_sources(
    research_pack: dict[str, Any] | None,
    report_draft: dict[str, Any] | None,
) -> dict[str, Any]:
    """把报告 citation key 精确映射到 SEC 官方来源，生成 SEC coverage。

    - ``official_sec_source_count``：research_pack 中带 locator 的官方 SEC 来源数；
    - ``has_official_sec_source``：是否存在这类来源；
    - ``official_sec_citation_coverage``：仅当能把报告的每一个 citation key 通过
      registry（src_<sha256(url)>）精确映射到 SEC 来源时才计算，结果为 [0,1]；
      无法逐一映射时写 None（禁止 clamp / 构造分母 / 用 citation_count 当分母）。
    """
    if not research_pack:
        return {
            "official_sec_source_count": 0,
            "has_official_sec_source": False,
            "official_sec_citation_coverage": None,
        }
    sources = [s for s in research_pack.get("sources") or [] if isinstance(s, dict)]
    sec_sources = [
        s
        for s in sources
        if any(host in str(s.get("canonical_url") or "") for host in SEC_OFFICIAL_HOSTS)
        and str(s.get("locator") or "").strip()
    ]
    keys = (report_draft or {}).get("citation_keys") or []
    valid_keys = [k for k in keys if isinstance(k, str) and k.strip()]
    if not valid_keys:
        return {
            "official_sec_source_count": len(sec_sources),
            "has_official_sec_source": len(sec_sources) > 0,
            "official_sec_citation_coverage": None,
        }
    # 精确映射：全部 key 必须都能映射到某个来源。
    url_by_key: dict[str, str] = {}
    for s in sources:
        url = str(s.get("canonical_url") or "")
        if not url:
            continue
        url_by_key.setdefault(_source_citation_key(url), url)
    mapped_sec: list[str] = []
    for key in valid_keys:
        url = url_by_key.get(key)
        if url is None or not any(host in url for host in SEC_OFFICIAL_HOSTS):
            return {
                "official_sec_source_count": len(sec_sources),
                "has_official_sec_source": len(sec_sources) > 0,
                "official_sec_citation_coverage": None,
            }
        mapped_sec.append(url)
    # 全部 key 都已精确映射到 SEC 来源：[0,1]，绝不 clamp、绝不构造分母。
    coverage = round(len(mapped_sec) / len(valid_keys), 4) if valid_keys else None
    return {
        "official_sec_source_count": len(sec_sources),
        "has_official_sec_source": len(sec_sources) > 0,
        "official_sec_citation_coverage": coverage,
    }


def _as_of_compliance(research_pack: dict[str, Any] | None, as_of: str) -> bool | None:
    """只检查决定信息可用性的发布日期（published_at / filing date）。

    - 所有可验证日期都不晚于 as_of 时返回 True；
    - 任一可验证发布日 > as_of 时返回 False；
    - 没有任何可验证发布日时返回 None（不得伪造 true/false）。
    抓取时间（accessed_at）不是财务信息发布日期，不计入合规判定。
    """
    if not research_pack:
        return None
    try:
        target = date.fromisoformat(as_of)
    except (TypeError, ValueError):
        return None
    found_date = False
    for src in research_pack.get("sources") or []:
        if not isinstance(src, dict):
            continue
        raw = src.get("published_at")
        if not raw:
            continue
        try:
            d = date.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        found_date = True
        if d > target:
            return False
    return True if found_date else None


def _has_live_external_evidence(manifest: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """manifest.evidence.invocation_summary 证明真实 SEC/Serper/LLM 调用。"""
    if not manifest:
        return False, ["manifest_missing"]
    evidence = manifest.get("evidence")
    inv = evidence.get("invocation_summary") if isinstance(evidence, dict) else None
    if not isinstance(inv, dict) or not inv:
        return False, ["manifest.evidence.invocation_summary_missing_or_empty"]
    sec_calls = sum(
        v
        for k, v in inv.items()
        if "sec_" in str(k) or "filing_" in str(k) or "company_resolver" in str(k)
    )
    search_calls = sum(v for k, v in inv.items() if "web_search" in str(k))
    if sec_calls <= 0 or search_calls <= 0:
        return False, [f"evidence 缺少真实外部调用: sec={sec_calls} search={search_calls}"]
    if not manifest.get("models"):
        return False, ["manifest.models_missing"]
    return True, []


def evaluate_technical_success(
    job: dict[str, Any],
    *,
    artifacts: dict[str, bytes],
    timeout_hit: bool,
) -> tuple[bool, list[str]]:
    """technical_success 判定（任务第六节口径 1）。"""
    failures: list[str] = []
    if job.get("status") != "succeeded":
        failures.append(f"final_status={job.get('status')!r}")
    if job.get("current_step") is not None:
        failures.append(f"current_step={job.get('current_step')!r}")
    steps = job.get("steps") or []
    running = [s for s in steps if isinstance(s, dict) and s.get("status") == "running"]
    if running:
        failures.append("running_step_present")
    required = REQUIRED_STEPS
    present_steps = {str(s.get("step_name")) for s in steps if isinstance(s, dict)}
    missing_steps = sorted(set(required) - present_steps)
    if missing_steps:
        failures.append("missing_steps:" + ",".join(missing_steps))
    for s in steps:
        if (
            isinstance(s, dict)
            and str(s.get("step_name")) in required
            and s.get("status") != "succeeded"
        ):
            failures.append(f"step_not_succeeded:{s.get('step_name')}")
    for key in REQUIRED_ARTIFACT_KEYS:
        content = artifacts.get(key)
        if content is None:
            failures.append(f"missing_artifact:{key}")
    md = artifacts.get(REPORT_MD_ARTIFACT)
    if md is not None and not md.decode("utf-8", errors="replace").strip():
        failures.append("report_md_empty")
    pdf = artifacts.get(REPORT_PDF_ARTIFACT)
    if pdf is not None and not pdf.startswith(b"%PDF-"):
        failures.append("pdf_bad_header")
    if timeout_hit:
        failures.append("timeout")
    # 敏感信息泄漏检查（工件内容）。
    for key, content in artifacts.items():
        if key in (REPORT_MD_ARTIFACT, REPORT_PDF_ARTIFACT):
            continue
        text = content.decode("utf-8", errors="replace")
        if any(
            secret in text.lower()
            for secret in ("api_key", "authorization", "cookie", "x-api-key", "sk-")
        ):
            failures.append(f"leaked_secret:{key}")
            break
    return (len(failures) == 0, failures)


def evaluate_live_acceptance(
    job: dict[str, Any],
    *,
    quality_report: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    research_pack: dict[str, Any] | None,
    report_draft: dict[str, Any] | None,
    as_of_date: str,
    artifacts: dict[str, bytes],
) -> tuple[bool, list[str]]:
    """live_acceptance 判定（technical 之上再追加质量/证据/引用/日期/可解析）。"""
    failures: list[str] = []
    if job.get("status") != "succeeded":
        failures.append("not_succeeded")
    ok, name_failures = _has_live_external_evidence(manifest)
    if not ok:
        failures.extend(name_failures)
    rec = (quality_report or {}).get("recommendation")
    if rec not in ACCEPTED_RECOMMENDATIONS:
        failures.append(f"recommendation={rec!r}（需要 published/publish_partial）")
    keys = (report_draft or {}).get("citation_keys") or []
    if not keys or not any(isinstance(k, str) and k.strip() for k in keys):
        failures.append("citation_keys_empty")
    if _official_sec_source_count(research_pack) < 1:
        failures.append("no_official_sec_source_with_locator")
    if _as_of_compliance(research_pack, as_of_date) is False:
        failures.append("data_after_as_of_date")
    # Research/Analysis/Writer/Quality/Manifest 工件均可解析。
    for key in (
        RESEARCH_PACK_ARTIFACT,
        ANALYSIS_PACK_ARTIFACT,
        REPORT_DRAFT_ARTIFACT,
        QUALITY_ARTIFACT,
        MANIFEST_ARTIFACT,
    ):
        if key in artifacts and _json_bytes(artifacts[key]) is None:
            failures.append(f"artifact_unparseable:{key}")
    return (len(failures) == 0, failures)


def evaluate_full_quality(
    live_acceptance_ok: bool,
    quality_report: dict[str, Any] | None,
    analysis_pack: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    """full_quality_pass 判定（任务第六节口径 3，绝不混用 published/publish_partial）。"""
    failures: list[str] = []
    if not live_acceptance_ok:
        failures.append("live_acceptance_failed")
    if (quality_report or {}).get("all_passed") is not True:
        failures.append("quality_all_passed_false")
    if (quality_report or {}).get("recommendation") != "published":
        failures.append("recommendation_not_published")
    completeness = (analysis_pack or {}).get("completeness")
    if completeness != "complete":
        failures.append(f"analysis_completeness={completeness!r}（需要 complete）")
    return (len(failures) == 0, failures)


def _annual_nodes_ready(job: dict[str, Any]) -> tuple[bool, list[str]]:
    snapshot = job.get("annual_nodes")
    if not isinstance(snapshot, dict):
        return False, ["annual_nodes_missing"]
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return False, ["annual_nodes_empty"]
    failures = [
        f"annual_node_not_succeeded:{node.get('node_key')}={node.get('status')}"
        for node in nodes
        if not isinstance(node, dict) or node.get("status") != "succeeded"
    ]
    if not isinstance(snapshot.get("critical_path_seconds"), (int, float)):
        failures.append("annual_critical_path_missing")
    return (not failures, failures)


def _fidelity_checks(
    rec: dict[str, Any],
    report_md: str,
    research_pack: dict[str, Any] | None,
    report_draft: dict[str, Any] | None,
    analysis_pack: dict[str, Any] | None,
    *,
    web_snapshot_by_url: dict[str, str] | None = None,
) -> None:
    """内容保真 + 引用可解析（v4：进评测记录，不进生产门禁；缺 pack 记 None 不误报）。"""
    from scripts.report_fidelity import (
        evaluate_citation_resolvability,
        evaluate_content_fidelity,
    )

    if isinstance(analysis_pack, dict) and (
        analysis_pack.get("facts") or analysis_pack.get("metrics")
    ):
        ok, _failures, detail = evaluate_content_fidelity(
            report_md,
            analysis_pack.get("facts") or [],
            analysis_pack.get("metrics") or [],
        )
        rec["content_fidelity_pass"] = ok
        rec["content_fidelity_detail"] = detail
    else:
        rec["content_fidelity_pass"] = None
        rec["content_fidelity_detail"] = {"skipped": "no_analysis_pack"}
    if isinstance(research_pack, dict) and isinstance(report_draft, dict):
        ok, _failures, detail = evaluate_citation_resolvability(
            research_pack, report_draft, web_snapshot_by_url=web_snapshot_by_url
        )
        rec["citation_resolvable"] = ok
        rec["citation_resolvability_detail"] = detail
    else:
        rec["citation_resolvable"] = None
        rec["citation_resolvability_detail"] = {"skipped": "no_research_pack_or_draft"}


def evaluate_annual_technical_success(
    job: dict[str, Any], *, artifacts: dict[str, bytes], timeout_hit: bool
) -> tuple[bool, list[str]]:
    """年度任务只读取年度 DAG 和受控工件，不套用 legacy workflow_steps。"""
    failures: list[str] = []
    if job.get("status") != "succeeded":
        failures.append(f"final_status={job.get('status')!r}")
    if job.get("research_mode") != "annual_deep":
        failures.append(f"research_mode={job.get('research_mode')!r}")
    nodes_ok, node_failures = _annual_nodes_ready(job)
    if not nodes_ok:
        failures.extend(node_failures)
    for key in ANNUAL_REQUIRED_ARTIFACT_KEYS:
        if key not in artifacts:
            failures.append(f"missing_artifact:{key}")
    markdown = artifacts.get(REPORT_MD_ARTIFACT, b"").decode("utf-8", errors="replace")
    if not markdown.strip():
        failures.append("report_md_empty")
    if "数据限制" not in markdown:
        failures.append("report_limitations_missing")
    pdf = artifacts.get(REPORT_PDF_ARTIFACT)
    if pdf is not None and not pdf.startswith(b"%PDF-"):
        failures.append("pdf_bad_header")
    if timeout_hit:
        failures.append("timeout")
    for key, content in artifacts.items():
        if key in (REPORT_MD_ARTIFACT, REPORT_PDF_ARTIFACT):
            continue
        text = content.decode("utf-8", errors="replace").lower()
        if any(secret in text for secret in ("api_key", "authorization", "cookie", "x-api-key")):
            failures.append(f"leaked_secret:{key}")
            break
    return (not failures, failures)


def evaluate_annual_live_acceptance(
    job: dict[str, Any],
    *,
    runtime_state: dict[str, Any] | None,
    facts: dict[str, Any] | None,
    facts_manifest: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    report_draft: dict[str, Any] | None,
    artifacts: dict[str, bytes],
) -> tuple[bool, list[str]]:
    """年度模式的证据验收：部分报告可交付，关键证据阻塞永不伪装成功。"""
    failures: list[str] = []
    if not runtime_state or runtime_state.get("finalization_status") not in {
        "ready_for_final_writer",
        "partial_ready_for_final_writer",
    }:
        failures.append("annual_finalization_not_deliverable")
    selected = facts.get("facts") if isinstance(facts, dict) else None
    if not isinstance(selected, list) or not selected:
        failures.append("annual_validated_facts_missing")
    if not facts_manifest or facts_manifest.get("status") != "completed":
        failures.append("annual_facts_manifest_not_completed")
    if not comparison or comparison.get("status") == "blocked":
        failures.append("annual_comparison_blocked")
    else:
        metrics = comparison.get("metrics")
        if not isinstance(metrics, list) or len(metrics) != 10:
            failures.append("annual_comparison_metrics_invalid")
    citations = (report_draft or {}).get("citation_keys") or []
    if not citations or not all(
        isinstance(key, str) and key.startswith(("src_", "fr_")) for key in citations
    ):
        failures.append("annual_citations_invalid")
    for key in ANNUAL_SECTION_ARTIFACTS:
        section = _json_bytes(artifacts.get(key))
        if not isinstance(section, dict) or not isinstance(section.get("draft"), dict):
            failures.append(f"annual_section_unparseable:{key}")
    return (not failures, failures)


# ---------------------------------------------------------------------------
# 单任务执行
# ---------------------------------------------------------------------------


def _parse_iso_datetime(value: Any) -> datetime | None:
    """把 API 返回的时间字符串解析为 aware datetime；无法解析时返回 None。"""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def run_one_job(
    api: ApiClient,
    *,
    plan: JobPlan,
    run_id: str,
    timeout_s: float,
    max_total_tokens: int,
    token_budget: dict[str, int],
    budget_lock: Optional[threading.Lock] = None,
    judge: Any | None = None,
) -> dict[str, Any]:
    """提交并轮询单个 job，返回完整原始记录（jobs.jsonl 一行）。

    时间口径（修复后）：
    - queue_duration ≈ client 提交时间 → Job.started_at（真实排队近似）；
      started_at 缺失时写 null，绝不把 POST API 耗时当作排队时间。
    - execution_duration = started_at → completed_at；任一缺失写 null。
    - e2e = 提交前（monotonic 起）→ 终态（monotonic 止）。
    """
    created_at = _now_iso()
    submit_mono = time.monotonic()
    key = idempotency_key(run_id, plan)
    payload = job_payload(plan)
    status_code, created = api.create_job(payload, key)
    if status_code == 409:
        # 契约限制：409 不返回 job_id（无法恢复）。按不重复付费原则记录为冲突。
        rec = _empty_record(run_id, plan, created_at)
        rec["terminal_status"] = "idempotency_conflict"
        rec["error_code"] = "IDEMPOTENCY_CONFLICT"
        rec["failure_stage"] = "client_submit"
        rec["finished_at"] = _now_iso()
        rec["error_message"] = _bounded_ascii(
            _redact("幂等冲突且 409 响应不含 job_id（API 契约限制，无法恢复 job_id）")
        )
        return rec
    if status_code not in (200, 202):
        rec = _empty_record(run_id, plan, created_at)
        rec["terminal_status"] = "failed"
        rec["error_code"] = "CLIENT_SUBMIT_ERROR"
        rec["failure_stage"] = "client_submit"
        rec["finished_at"] = _now_iso()
        rec["error_message"] = _bounded_ascii(
            _redact(str(created.get("detail") or created.get("error") or "submit failed"))
        )
        return rec

    idempotency_replayed = status_code == 200
    job_id = str(created.get("job_id"))
    deadline = time.monotonic() + timeout_s
    final_job: Optional[dict[str, Any]] = None
    timeout_hit = False
    while time.monotonic() < deadline:
        time.sleep(2.0)
        sc, data = api.get_job(job_id)
        if sc != 200:
            time.sleep(1.0)
            continue
        final_job = data
        if data.get("status") in TERMINAL_JOB_STATES:
            break
    else:
        timeout_hit = True

    finished_mono = time.monotonic()
    rec = _empty_record(run_id, plan, created_at)
    rec["job_id"] = job_id
    rec["idempotency_replayed"] = idempotency_replayed
    if final_job is not None:
        started_dt = _parse_iso_datetime(final_job.get("started_at"))
        completed_dt = _parse_iso_datetime(final_job.get("completed_at"))
        rec.update(_job_to_record(final_job))
        if started_dt is not None:
            created_dt = _parse_iso_datetime(created_at)
            if created_dt is not None and not idempotency_replayed:
                queue_seconds = (started_dt - created_dt).total_seconds()
                rec["queue_duration_seconds"] = round(max(queue_seconds, 0.0), 3)
            if completed_dt is not None:
                rec["execution_duration_seconds"] = round(
                    (completed_dt - started_dt).total_seconds(), 3
                )
        # started_at 缺失时 queue/execution 保持 null（不伪造）。
    else:
        rec["terminal_status"] = "failed"
        rec["error_code"] = "CLIENT_POLL_TIMEOUT"
        rec["failure_stage"] = "client_poll"

    rec["e2e_duration_seconds"] = (
        None if idempotency_replayed else round(finished_mono - submit_mono, 3)
    )
    rec["timeout"] = timeout_hit

    # 工件下载与三层判定。
    artifacts: dict[str, bytes] = {}
    required_keys = (
        ANNUAL_REQUIRED_ARTIFACT_KEYS
        if plan.research_mode == "annual_deep"
        else REQUIRED_ARTIFACT_KEYS
    )
    api.list_artifacts(job_id)
    for key in required_keys:
        status_code, content = api.download_artifact(job_id, key)
        if status_code == 200:
            artifacts[key] = content
    if artifacts:
        rec["required_artifacts"] = sorted(artifacts.keys())
    missing = sorted(set(required_keys) - set(artifacts))
    rec["missing_artifacts"] = missing

    quality_report = _json_bytes(artifacts.get(QUALITY_ARTIFACT))
    manifest = _json_bytes(artifacts.get(MANIFEST_ARTIFACT))
    research_pack = _json_bytes(artifacts.get(RESEARCH_PACK_ARTIFACT))
    report_draft = _json_bytes(artifacts.get(REPORT_DRAFT_ARTIFACT))
    analysis_pack = _json_bytes(artifacts.get(ANALYSIS_PACK_ARTIFACT))

    rec["quality_recommendation"] = (quality_report or {}).get("recommendation")
    rec["quality_all_passed"] = (quality_report or {}).get("all_passed")
    rec["annual_partial_delivery"] = (
        plan.research_mode == "annual_deep"
        and rec["quality_recommendation"] == "publish_partial"
    )
    rec["analysis_completeness"] = (analysis_pack or {}).get("completeness")
    rec["revision_attempted"] = bool(manifest and manifest.get("revision_attempted"))
    rec["revision_succeeded"] = bool(manifest and manifest.get("revision_succeeded"))
    rec["report_chars"] = len(
        artifacts.get(REPORT_MD_ARTIFACT, b"").decode("utf-8", errors="replace")
    )
    cit = _count_citations(report_draft)
    rec.update(cit)
    rec["invalid_citation_count"] = cit["citation_count"] - cit["valid_citation_count"]
    sec_stats = _map_citations_to_sec_sources(research_pack, report_draft)
    rec["official_sec_source_count"] = sec_stats["official_sec_source_count"]
    rec["has_official_sec_source"] = sec_stats["has_official_sec_source"]
    rec["official_sec_citation_coverage"] = sec_stats["official_sec_citation_coverage"]
    rec["as_of_compliant"] = _as_of_compliance(research_pack, plan.as_of_date)

    # v4：内容保真 + 引用可解析（进评测记录，不进生产门禁）。
    report_md = artifacts.get(REPORT_MD_ARTIFACT, b"").decode("utf-8", errors="replace")
    _fidelity_checks(rec, report_md, research_pack, report_draft, analysis_pack)
    if judge is not None:
        rec["judge_score"] = judge.score(report_md)

    # Token / LLM / 外部调用（尽力从 manifest.performance 读取；缺失写 null 不伪造）。
    # 真实 manifest 字段为 performance.token_usage.{prompt/completion/total/cached_prompt}_tokens。
    perf = manifest.get("performance") if isinstance(manifest, dict) else None
    if isinstance(perf, dict):
        usage = perf.get("token_usage") if isinstance(perf.get("token_usage"), dict) else None
        if isinstance(usage, dict):
            rec["input_tokens"] = usage.get("prompt_tokens")
            rec["output_tokens"] = usage.get("completion_tokens")
            rec["cached_input_tokens"] = usage.get("cached_prompt_tokens")
            rec["total_tokens"] = usage.get("total_tokens")
            rec["token_usage_complete"] = (
                isinstance(rec["input_tokens"], (int, float))
                and isinstance(rec["output_tokens"], (int, float))
                and isinstance(rec["total_tokens"], (int, float))
            )
            rec["llm_calls_total"] = (
                perf.get("llm_calls") if "llm_calls" in perf else perf.get("llm_call_count")
            )
        else:
            rec["token_usage_complete"] = False
        rec["external_api_calls"] = perf.get("external_api_calls")
        rec["retries"] = perf.get("retries")
        cache = perf.get("tool_cache") if isinstance(perf.get("tool_cache"), dict) else None
        if isinstance(cache, dict):
            rec["cache_hits"] = cache.get("hits")
            rec["cache_misses"] = cache.get("misses")
        inv = (
            manifest.get("evidence", {}).get("invocation_summary")
            if isinstance(manifest.get("evidence"), dict)
            else None
        )
        if isinstance(inv, dict):
            rec["external_api_calls"] = sum(
                int(v) for v in inv.values() if isinstance(v, (int, float))
            )
    else:
        rec["token_usage_complete"] = False

    rec["success_checks"] = {
        "technical_success": False,
        "live_acceptance_success": False,
        "full_quality_success": False,
    }
    if plan.research_mode == "annual_deep":
        tech_ok, tech_failures = evaluate_annual_technical_success(
            final_job or {}, artifacts=artifacts, timeout_hit=timeout_hit
        )
    else:
        tech_ok, tech_failures = evaluate_technical_success(
            final_job or {}, artifacts=artifacts, timeout_hit=timeout_hit
        )
    if tech_ok:
        rec["technical_success"] = True
        if plan.research_mode == "annual_deep":
            live_ok, live_failures = evaluate_annual_live_acceptance(
                final_job or {},
                runtime_state=_json_bytes(artifacts.get(ANNUAL_RUNTIME_ARTIFACT)),
                facts=_json_bytes(artifacts.get(ANNUAL_FACTS_ARTIFACT)),
                facts_manifest=_json_bytes(artifacts.get(ANNUAL_FACTS_MANIFEST_ARTIFACT)),
                comparison=_json_bytes(artifacts.get(ANNUAL_COMPARISON_ARTIFACT)),
                report_draft=report_draft,
                artifacts=artifacts,
            )
        else:
            live_ok, live_failures = evaluate_live_acceptance(
                final_job or {},
                quality_report=quality_report,
                manifest=manifest,
                research_pack=research_pack,
                report_draft=report_draft,
                as_of_date=plan.as_of_date,
                artifacts=artifacts,
            )
        if live_ok:
            rec["live_acceptance_success"] = True
            full_ok, full_failures = evaluate_full_quality(True, quality_report, analysis_pack)
            if full_ok:
                rec["full_quality_success"] = True
            else:
                rec["evaluate_full_quality_failures"] = full_failures
        else:
            rec["evaluate_live_acceptance_failures"] = live_failures
    else:
        rec["evaluate_technical_failures"] = tech_failures
    rec["success_checks"] = {
        "technical_success": bool(rec.get("technical_success")),
        "live_acceptance_success": bool(rec.get("live_acceptance_success")),
        "full_quality_success": bool(rec.get("full_quality_success")),
    }

    # 任务间 best-effort token 熔断（只基于真实 usage；并发下用 budget_lock 串行化）。
    total_tokens = rec.get("total_tokens")
    if isinstance(total_tokens, (int, float)):
        if budget_lock is not None:
            with budget_lock:
                token_budget["used"] += int(total_tokens)
                if max_total_tokens > 0 and token_budget["used"] > max_total_tokens:
                    rec["budget_breached"] = True
        else:
            token_budget["used"] += int(total_tokens)
            if max_total_tokens > 0 and token_budget["used"] > max_total_tokens:
                rec["budget_breached"] = True

    if not (rec.get("technical_success") or rec.get("terminal_status") == "succeeded"):
        rec["error_code"] = (
            rec.get("error_code") or _first_step_error(final_job) or "TECHNICAL_FAILURE"
        )
        rec["failure_stage"] = rec.get("failure_stage") or _first_failed_step(final_job)
    return rec


def _empty_record(run_id: str, plan: JobPlan, created_at: str) -> dict[str, Any]:
    """构造 jobs.jsonl 固定字段骨架（缺失字段一律 null，不填猜测值）。"""
    return {
        "benchmark_run_id": run_id,
        "suite": plan.suite,
        "case_id": plan.case_id,
        "ticker": plan.ticker,
        "sector": plan.sector,
        "profile": plan.profile,
        "research_mode": plan.research_mode,
        "as_of_date": plan.as_of_date,
        "repetition": plan.repetition,
        "pair_id": plan.pair_id,
        "execution_order": plan.execution_order,
        "configured_concurrency": plan.configured_concurrency,
        "job_id": None,
        "created_at": created_at,
        "started_at": None,
        "finished_at": None,
        "queue_duration_seconds": None,
        "execution_duration_seconds": None,
        "e2e_duration_seconds": None,
        "terminal_status": None,
        "idempotency_replayed": False,
        "current_step": None,
        "timeout": False,
        "error_code": None,
        "failure_stage": None,
        "quality_recommendation": None,
        "quality_all_passed": None,
        "analysis_completeness": None,
        "revision_attempted": False,
        "revision_succeeded": False,
        "required_artifacts": [],
        "missing_artifacts": [],
        "report_chars": None,
        "citation_count": 0,
        "valid_citation_count": 0,
        "invalid_citation_count": 0,
        "official_sec_source_count": 0,
        "has_official_sec_source": False,
        "official_sec_citation_coverage": None,
        "as_of_compliant": None,
        "content_fidelity_pass": None,
        "content_fidelity_detail": None,
        "citation_resolvable": None,
        "citation_resolvability_detail": None,
        "judge_score": None,
        "llm_calls_total": None,
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "total_tokens": None,
        "token_usage_complete": False,
        "external_api_calls": None,
        "retries": None,
        "cache_hits": None,
        "cache_misses": None,
        "annual_critical_path_seconds": None,
        "annual_node_statuses": {},
        "annual_partial_delivery": False,
        "success_checks": {},
        "technical_success": False,
        "live_acceptance_success": False,
        "full_quality_success": False,
    }


def _job_to_record(job: dict[str, Any]) -> dict[str, Any]:
    out = {
        "terminal_status": job.get("status"),
        "current_step": job.get("current_step"),
        "started_at": _iso_of(job.get("started_at")),
        "finished_at": _iso_of(job.get("completed_at")),
        "error_code": job.get("error_code"),
        "failure_stage": job.get("failure_stage"),
    }
    annual_nodes = job.get("annual_nodes")
    if isinstance(annual_nodes, dict):
        out["annual_critical_path_seconds"] = annual_nodes.get("critical_path_seconds")
        nodes = annual_nodes.get("nodes")
        if isinstance(nodes, list):
            out["annual_node_statuses"] = {
                str(node.get("node_key")): str(node.get("status"))
                for node in nodes
                if isinstance(node, dict) and node.get("node_key")
            }
    return out


def _iso_of(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _first_step_error(job: dict[str, Any] | None) -> str | None:
    for s in (job or {}).get("steps") or []:
        if isinstance(s, dict) and s.get("error_code"):
            return str(s["error_code"])
    return None


def _first_failed_step(job: dict[str, Any] | None) -> str | None:
    for s in (job or {}).get("steps") or []:
        if isinstance(s, dict) and s.get("status") in ("failed_retryable", "failed_terminal"):
            return str(s.get("step_name"))
    return None


# ---------------------------------------------------------------------------
# 汇总与报表
# ---------------------------------------------------------------------------


def _build_summary(
    run_id: str,
    suite: str,
    records: list[dict[str, Any]],
    config: dict[str, Any],
    metrics_diff: dict[str, Any],
    metrics_before: dict[str, Any] | None,
    metrics_after: dict[str, Any] | None,
) -> dict[str, Any]:
    terminal = [r for r in records if r.get("terminal_status") in TERMINAL_JOB_STATES]
    succeeded = [r for r in terminal if r.get("terminal_status") == "succeeded"]
    main_records = [r for r in terminal if r.get("terminal_status") != "idempotency_conflict"]

    full = records if suite == "load" else main_records
    summary: dict[str, Any] = {
        "benchmark_run_id": run_id,
        "suite": suite,
        "generated_at": _now_iso(),
        "config": config,
        "counts": {
            "planned": len(records),
            "terminal": len(terminal),
            "succeeded": len(succeeded),
            "failed": sum(1 for r in terminal if r.get("terminal_status") == "failed"),
            "partial": sum(1 for r in terminal if r.get("terminal_status") == "partial"),
            "cancelled": sum(1 for r in terminal if r.get("terminal_status") == "cancelled"),
            "timeout": sum(1 for r in full if r.get("timeout")),
            "idempotency_conflict": sum(
                1 for r in records if r.get("terminal_status") == "idempotency_conflict"
            ),
        },
        "success_rates": {
            "technical_success_rate": rate_stats(full, "technical_success"),
            "live_acceptance_rate": rate_stats(full, "live_acceptance_success"),
            "full_quality_pass_rate": rate_stats(full, "full_quality_success"),
        },
        "recommendation_distribution": _dist(full, "quality_recommendation"),
        "error_code_distribution": _dist(terminal, "error_code"),
        "failure_stage_distribution": _dist(terminal, "failure_stage"),
        "analysis_completeness_distribution": _dist(full, "analysis_completeness"),
        "latency": {
            field: aggregate_durations(full, field)
            for field in (
                "queue_duration_seconds",
                "execution_duration_seconds",
                "e2e_duration_seconds",
            )
        },
        "small_sample_p95_warning": len(full) < 30,
        "throughput_jobs_per_minute": _throughput(full),
        "token_usage": _token_summary(full),
        "cost": _cost_summary(full, config.get("pricing_file")),
        "reliability": {
            "cache_hit_rate": _ratio(full, "cache_hits", "cache_misses"),
            "usage_missing_rate": None,
            "citation_valid_rate": _ratio(full, "valid_citation_count", "citation_count"),
            "official_sec_source_coverage": _avg_coverage(full, "official_sec_citation_coverage"),
            "official_sec_source_count_total": sum(
                int(r.get("official_sec_source_count") or 0) for r in full
            ),
            "has_official_sec_source_rate": rate_stats(full, "has_official_sec_source"),
            "as_of_compliance_rate": _as_of_rate(full),
            "content_fidelity_rate": fidelity_rate(full, "content_fidelity_pass"),
            "citation_resolvable_rate": fidelity_rate(full, "citation_resolvable"),
        },
        "metrics_snapshot_available": metrics_before is not None and metrics_after is not None,
        "prometheus_metrics_diff": metrics_diff,
        "claim_candidates": [],
    }
    # usage missing rate（token_usage_complete=False 的比例）。
    usage_total = len(full)
    usage_missing = sum(1 for r in full if r.get("token_usage_complete") is not True)
    summary["reliability"]["usage_missing_rate"] = (
        round(usage_missing / usage_total, 4) if usage_total else None
    )
    summary["token_usage"]["usage_complete_rate"] = (
        round((usage_total - usage_missing) / usage_total, 4) if usage_total else None
    )
    if suite == "annual-paired":
        by_mode: dict[str, list[dict[str, Any]]] = {"legacy": [], "annual_deep": []}
        for record in full:
            mode = str(record.get("research_mode") or "legacy")
            if mode in by_mode:
                by_mode[mode].append(record)
        summary["mode_comparison"] = {
            mode: {
                "jobs": len(group),
                "technical_success": rate_stats(group, "technical_success"),
                "live_acceptance": rate_stats(group, "live_acceptance_success"),
                "full_quality_pass": rate_stats(group, "full_quality_success"),
                "e2e_duration": aggregate_durations(group, "e2e_duration_seconds"),
                "annual_critical_path": aggregate_durations(group, "annual_critical_path_seconds"),
                "partial_deliveries": sum(
                    1 for item in group if item.get("annual_partial_delivery")
                ),
                "blocked_or_failed": sum(
                    1 for item in group if item.get("terminal_status") != "succeeded"
                ),
            }
            for mode, group in by_mode.items()
        }
    return summary


def _dist(records: Sequence[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        value = r.get(field)
        key = str(value) if value is not None else "(None)"
        out[key] = out.get(key, 0) + 1
    return out


def _throughput(records: Sequence[dict[str, Any]]) -> float | None:
    durations = [
        r.get("e2e_duration_seconds") for r in records if r.get("e2e_duration_seconds") is not None
    ]
    if not durations:
        return None
    total = sum(float(d) for d in durations)
    if total <= 0:
        return None
    return round(len(durations) * 60.0 / total, 4)


def _ratio(records: Sequence[dict[str, Any]], num_field: str, den_field: str) -> float | None:
    num = sum(float(r.get(num_field) or 0) for r in records)
    den = sum(float(r.get(den_field) or 0) for r in records)
    if den <= 0:
        return None
    return round(num / den, 4)


def _avg_coverage(records: Sequence[dict[str, Any]], field: str) -> float | None:
    """对每个都能精确映射的 coverage 求平均；一个都没有时返回 None（不构造分母）。"""
    values = [float(r[field]) for r in records if isinstance(r.get(field), (int, float))]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _as_of_rate(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """as_of 合规率：只把有可验证发布日的记录计入分母；无日期记录单独统计。"""
    verifiable = [r for r in records if r.get("as_of_compliant") is not None]
    unknown = sum(1 for r in records if r.get("as_of_compliant") is None)
    stats = rate_stats(verifiable, "as_of_compliant")
    stats["n_without_verifiable_date"] = unknown
    return stats


def _token_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"):
        values = [float(r[field]) for r in records if r.get(field) is not None and r[field] != ""]
        out[field] = {
            "total": round(sum(values), 2) if values else 0,
            "n_with_usage": len(values),
            "avg_per_job": round(sum(values) / len(values), 2) if values else None,
        }
    full_quality = [r for r in records if r.get("full_quality_success")]
    out["avg_tokens_per_full_quality_job"] = (
        round(
            sum(float(r["total_tokens"]) for r in full_quality if r.get("total_tokens") is not None)
            / len(full_quality),
            2,
        )
        if full_quality
        else None
    )
    out["full_quality_job_count"] = len(full_quality)
    return out


def _cost_summary(records: Sequence[dict[str, Any]], pricing_file: Path | None) -> dict[str, Any]:
    """只有提供带日期和模型名的 pricing JSON 才计算 estimated_cost_usd；否则 null。"""
    if pricing_file is None or not pricing_file.exists():
        return {
            "pricing_file": None,
            "estimated_cost_usd": None,
            "per_job_avg_usd": None,
            "per_successful_job_avg_usd": None,
            "note": "未提供 pricing-file，不计算成本（不在代码中硬编码模型价格）",
        }
    try:
        pricing = json.loads(pricing_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {
            "pricing_file": str(pricing_file),
            "estimated_cost_usd": None,
            "per_job_avg_usd": None,
            "per_successful_job_avg_usd": None,
            "note": "pricing-file 无法解析，成本输出 null",
        }
    if not isinstance(pricing, dict) or "models" not in pricing or "as_of" not in pricing:
        return {
            "pricing_file": str(pricing_file),
            "estimated_cost_usd": None,
            "per_job_avg_usd": None,
            "per_successful_job_avg_usd": None,
            "note": "pricing-file 缺少 as_of/models 字段，成本输出 null",
        }
    total = 0.0
    n = 0
    for r in records:
        cost = estimate_job_cost(r, pricing)
        if cost is None:
            continue
        total += cost
        n += 1
    per_job = round(total / n, 6) if n else None
    successful = [r for r in records if r.get("technical_success")]
    per_success = (
        round(sum(estimate_job_cost(r, pricing) or 0 for r in successful) / len(successful), 6)
        if successful
        else None
    )
    return {
        "pricing_file": str(pricing_file),
        "pricing_as_of": pricing.get("as_of"),
        "estimated_cost_usd": round(total, 6) if n else None,
        "per_job_avg_usd": per_job,
        "per_successful_job_avg_usd": per_success,
    }


def estimate_job_cost(record: dict[str, Any], pricing: dict[str, Any]) -> float | None:
    """按模型价格估算单个 job 成本；总 token 不可得时返回 None（不伪造）。

    计价数学委托给共享模块 invest_research.application.costing（单一来源）。
    """
    import invest_research.application.costing as costing

    total = record.get("total_tokens")
    if not isinstance(total, (int, float)) or total is None:
        return None
    entry = costing.pricing_entry_for(pricing, record.get("profile"))
    return costing.estimate_job_cost(
        input_tokens=record.get("input_tokens"),
        output_tokens=record.get("output_tokens"),
        pricing_entry=entry,
    )


def _paired_comparison(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 pair_id 配对 fast/deep，生成 paired_comparison.csv 行。"""
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for r in records:
        if r.get("suite") != "paired" or not r.get("pair_id"):
            continue
        by_pair.setdefault(str(r["pair_id"]), {})[str(r["profile"])] = r
    rows: list[dict[str, Any]] = []
    for pair_id in sorted(by_pair):
        pair = by_pair[pair_id]
        fast = pair.get("fast")
        deep = pair.get("deep")
        if fast is None or deep is None:
            continue
        rows.append(
            {
                "pair_id": pair_id,
                "ticker": fast.get("ticker"),
                "sector": fast.get("sector"),
                "repetition": fast.get("repetition"),
                "fast_e2e_s": fast.get("e2e_duration_seconds"),
                "deep_e2e_s": deep.get("e2e_duration_seconds"),
                "e2e_pct_change_fast_vs_deep": pct_change(
                    deep.get("e2e_duration_seconds"), fast.get("e2e_duration_seconds")
                ),
                "fast_execution_s": fast.get("execution_duration_seconds"),
                "deep_execution_s": deep.get("execution_duration_seconds"),
                "execution_pct_change": pct_change(
                    deep.get("execution_duration_seconds"), fast.get("execution_duration_seconds")
                ),
                "fast_total_tokens": fast.get("total_tokens"),
                "deep_total_tokens": deep.get("total_tokens"),
                "token_pct_change": pct_change(deep.get("total_tokens"), fast.get("total_tokens")),
                "fast_technical_success": fast.get("technical_success"),
                "deep_technical_success": deep.get("technical_success"),
                "fast_live_acceptance_success": fast.get("live_acceptance_success"),
                "deep_live_acceptance_success": deep.get("live_acceptance_success"),
                "fast_full_quality_success": fast.get("full_quality_success"),
                "deep_full_quality_success": deep.get("full_quality_success"),
            }
        )
    return rows


def _annual_mode_paired_comparison(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 pair_id 生成 legacy/annual_deep 对照行；不复用 fast/deep 字段语义。"""
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        if record.get("suite") != "annual-paired" or not record.get("pair_id"):
            continue
        by_pair.setdefault(str(record["pair_id"]), {})[str(record.get("research_mode"))] = record
    rows: list[dict[str, Any]] = []
    for pair_id in sorted(by_pair):
        pair = by_pair[pair_id]
        legacy = pair.get("legacy")
        annual = pair.get("annual_deep")
        if legacy is None or annual is None:
            continue
        rows.append(
            {
                "pair_id": pair_id,
                "ticker": legacy.get("ticker"),
                "sector": legacy.get("sector"),
                "repetition": legacy.get("repetition"),
                "legacy_e2e_s": legacy.get("e2e_duration_seconds"),
                "annual_deep_e2e_s": annual.get("e2e_duration_seconds"),
                "e2e_pct_change_annual_vs_legacy": pct_change(
                    legacy.get("e2e_duration_seconds"), annual.get("e2e_duration_seconds")
                ),
                "annual_critical_path_s": annual.get("annual_critical_path_seconds"),
                "legacy_technical_success": legacy.get("technical_success"),
                "annual_technical_success": annual.get("technical_success"),
                "legacy_live_acceptance": legacy.get("live_acceptance_success"),
                "annual_live_acceptance": annual.get("live_acceptance_success"),
                "annual_partial_delivery": annual.get("annual_partial_delivery"),
                "legacy_total_tokens": legacy.get("total_tokens"),
                "annual_total_tokens": annual.get("total_tokens"),
            }
        )
    return rows


def _company_breakdown(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    annual_mode_split = any(record.get("suite") == "annual-paired" for record in records)
    by_ticker: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for r in records:
        if r.get("suite") == "load":
            continue
        key = (str(r.get("ticker")), str(r.get("research_mode"))) if annual_mode_split else (
            str(r.get("ticker")),
            None,
        )
        by_ticker.setdefault(key, []).append(r)
    for (ticker, research_mode) in sorted(by_ticker):
        group = by_ticker[(ticker, research_mode)]
        row: dict[str, Any] = {
                "ticker": ticker,
                "sector": group[0].get("sector"),
                "jobs": len(group),
                "technical_success": rate_stats(group, "technical_success"),
                "live_acceptance": rate_stats(group, "live_acceptance_success"),
                "full_quality_pass": rate_stats(group, "full_quality_success"),
                "avg_e2e_s": round(
                    sum(
                        float(r["e2e_duration_seconds"])
                        for r in group
                        if r.get("e2e_duration_seconds") is not None
                    )
                    / len([r for r in group if r.get("e2e_duration_seconds") is not None]),
                    3,
                )
                if any(r.get("e2e_duration_seconds") is not None for r in group)
                else None,
                "failed_jobs": [
                    str(r.get("job_id"))
                    for r in group
                    if r.get("terminal_status") not in (None, "succeeded")
                ],
            }
        if research_mode is not None:
            row["research_mode"] = research_mode
        rows.append(row)
    return rows


def _claim_eligibility(
    records: Sequence[dict[str, Any]],
    *,
    repeats: int,
    recommended_repeats: int = 3,
    minimum_complete_pairs: int = 30,
    minimum_unique_companies: int = 10,
) -> dict[str, Any]:
    """判断是否满足输出"fast 降低 XX% 延迟/Token，质量基本不下降"候选结论的条件。

    只输出观察结果和限制；绝不自动生成宣传性简历语句。
    """
    paired = [r for r in records if r.get("suite") == "paired"]
    fast = [r for r in paired if r.get("profile") == "fast"]
    deep = [r for r in paired if r.get("profile") == "deep"]

    # as_of_compliant 由 run_one_job 按每条记录独立计算并写入；
    # 这里绝不再覆盖（不再因 technical 短路而默认为 false）。

    usage_complete = [r for r in paired if r.get("token_usage_complete")]
    coverage = round(len(usage_complete) / len(paired), 4) if paired else 0.0

    fast_live = rate_stats(fast, "live_acceptance_success")
    deep_live = rate_stats(deep, "live_acceptance_success")
    fast_full = rate_stats(fast, "full_quality_success")
    deep_full = rate_stats(deep, "full_quality_success")
    complete_pairs = {
        str(record.get("pair_id"))
        for record in fast
        if record.get("pair_id")
        and any(
            candidate.get("pair_id") == record.get("pair_id")
            and candidate.get("full_quality_success")
            for candidate in deep
        )
        and record.get("full_quality_success")
    }
    unique_companies = {
        str(record.get("ticker"))
        for record in paired
        if str(record.get("pair_id")) in complete_pairs
    }

    live_delta = None
    full_delta = None
    if fast_live["rate"] is not None and deep_live["rate"] is not None:
        live_delta = round((fast_live["rate"] or 0) - (deep_live["rate"] or 0), 4)
    if fast_full["rate"] is not None and deep_full["rate"] is not None:
        full_delta = round((fast_full["rate"] or 0) - (deep_full["rate"] or 0), 4)

    fast_minus_deep_live_pp = None
    if live_delta is not None:
        fast_minus_deep_live_pp = live_delta * 100.0
    conditions = {
        "token_usage_coverage_ge_95pct": coverage >= 0.95,
        "fast_and_deep_live_acceptance_have_real_denominators": (
            fast_live["denominator"] > 0 and deep_live["denominator"] > 0
        ),
        "fast_live_acceptance_not_lower_by_more_than_5pp": fast_minus_deep_live_pp is not None
        and fast_minus_deep_live_pp >= -5.0,
        "fast_full_quality_not_lower_by_more_than_10pp": (
            full_delta is not None and full_delta >= -0.10
        ),
        "recommended_repeats_completed": repeats >= recommended_repeats,
        "minimum_complete_pairs_reached": len(complete_pairs) >= minimum_complete_pairs,
        "minimum_unique_companies_reached": len(unique_companies) >= minimum_unique_companies,
        "no_unexplained_systemic_failure_or_cache_order_bias": True,  # 由报告人工复核
    }
    eligible = all(conditions.values())
    return {
        "claim_eligible": eligible,
        "conditions": conditions,
        "token_usage_coverage": coverage,
        "fast_live_acceptance_rate": fast_live,
        "deep_live_acceptance_rate": deep_live,
        "fast_full_quality_pass_rate": fast_full,
        "deep_full_quality_pass_rate": deep_full,
        "paired_jobs": len(paired),
        "recommended_repeats": recommended_repeats,
        "completed_fast_deep_pairs": len(complete_pairs),
        "unique_companies_with_complete_pairs": len(unique_companies),
    }


def _annual_claim_eligibility(
    records: Sequence[dict[str, Any]], *, repeats: int
) -> dict[str, Any]:
    """两公司两轮仅是 canary：保留观察事实，明确禁止生成简历性能结论。"""
    paired = [record for record in records if record.get("suite") == "annual-paired"]
    annual = [record for record in paired if record.get("research_mode") == "annual_deep"]
    legacy = [record for record in paired if record.get("research_mode") == "legacy"]
    usage_coverage = (
        round(sum(1 for record in paired if record.get("token_usage_complete")) / len(paired), 4)
        if paired
        else 0.0
    )
    return {
        "claim_eligible": False,
        "canary_only": True,
        "conditions": {
            "two_company_two_repeat_canary": repeats == 2 and len(paired) == 8,
            "token_usage_coverage_ge_95pct": usage_coverage >= 0.95,
            "resume_claims_prohibited_until_expanded_sample": False,
        },
        "token_usage_coverage": usage_coverage,
        "legacy_live_acceptance_rate": rate_stats(legacy, "live_acceptance_success"),
        "annual_live_acceptance_rate": rate_stats(annual, "live_acceptance_success"),
        "paired_jobs": len(paired),
        "note": "首轮只验证评测链路、年度关键路径与安全降级；不得据此声称性能收益。",
    }


def _render_report(
    run_dir: Path,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    paired_rows: list[dict[str, Any]],
    company_rows: list[dict[str, Any]],
    environment: dict[str, Any],
    eligibility: dict[str, Any],
) -> str:
    if summary.get("suite") == "annual-paired":
        return _render_annual_mode_report(
            run_dir, summary, paired_rows, company_rows, environment, eligibility
        )
    lines: list[str] = []
    lines.append("# P06-11 Live Portfolio Benchmark Report")
    lines.append("")
    lines.append(f"- benchmark_run_id: `{summary['benchmark_run_id']}`")
    lines.append(f"- suite: `{summary['suite']}`")
    lines.append(f"- generated_at: {summary['generated_at']}")
    lines.append("")
    lines.append("## 1. 重要声明")
    lines.append("")
    lines.append(
        "- 本报告是 **live agent benchmark**（FLOW_MODE=live，真实 SEC/Serper/LLM 调用）。"
    )
    lines.append(
        "- 既有的 **fake workflow benchmark**（P06-10，100/100 成功）测量的是 "
        "API→Redis→Celery→Worker→DB→Flow→工件链路，**不是** 真实 Agent 成功率。"
    )
    lines.append(
        "- 本报告的 technical_success_rate / live_acceptance_rate / "
        "full_quality_pass_rate 才是真实 live 成功率口径。"
    )
    lines.append(
        "- 只有 claim_candidates.json 中 eligibility=true 的条目才可用于简历表述；"
        "否则只能作为观察。"
    )
    lines.append("")
    lines.append("## 2. 测试环境")
    lines.append("")
    for key, value in environment.items():
        lines.append(f"- **{key}**: `{value}`")
    lines.append("")
    lines.append("## 3. 主指标")
    lines.append("")
    lines.append("| 指标 | 分子 | 分母 | 比率 | Wilson 95% 区间 |")
    lines.append("|---|---|---|---|---|")
    for name, stats in summary["success_rates"].items():
        rate = f"{stats['rate']:.4f}" if stats["rate"] is not None else "None"
        lo = "None" if stats["wilson_low"] is None else f"{stats['wilson_low']:.4f}"
        hi = "None" if stats["wilson_high"] is None else f"{stats['wilson_high']:.4f}"
        lines.append(
            f"| {name} | {stats['numerator']} | {stats['denominator']} | {rate} | [{lo}, {hi}] |"
        )
    lines.append("")
    lines.append("## 4. 延迟与吞吐")
    lines.append("")
    for field, agg in summary["latency"].items():
        label = field.replace("_duration_seconds", "")
        lines.append(
            f"- **{label}** (n={agg['n']}): avg={agg['avg']}s min={agg['min']}s max={agg['max']}s "
            f"P50={agg['p50']}s P90={agg['p90']}s P95={agg['p95']}s P99={agg['p99']}s"
        )
    if summary.get("small_sample_p95_warning"):
        lines.append("- ⚠️ 小样本：P95/P99 标注为 **不稳定，仅作观察**。")
    lines.append(f"- throughput: {summary['throughput_jobs_per_minute']} jobs/minute")
    lines.append("")
    lines.append("## 5. Token 与成本")
    lines.append("")
    token = summary["token_usage"]
    lines.append(
        f"- usage 完整率: {summary['token_usage'].get('usage_complete_rate')!r}（"
        "<0.95 时不得生成'节省 XX% Token/费用'的简历结论）"
    )
    lines.append(
        f"- 每任务 Token (avg): input={token['input_tokens']['avg_per_job']} "
        f"output={token['output_tokens']['avg_per_job']} "
        f"total={token['total_tokens']['avg_per_job']}"
    )
    lines.append(
        f"- 每个 full-quality 任务 Token avg: {token['avg_tokens_per_full_quality_job']}（"
        f"n={token['full_quality_job_count']}）"
    )
    cost = summary["cost"]
    lines.append(
        f"- estimated_cost_usd: {cost['estimated_cost_usd']}（"
        f"pricing-file: {cost['pricing_file']}）"
    )
    lines.append(
        f"- 每任务成本 avg: {cost['per_job_avg_usd']}；"
        f"每成功任务成本 avg: {cost['per_successful_job_avg_usd']}"
    )
    lines.append("")
    lines.append("## 6. 质量与可靠性")
    lines.append("")
    rel = summary["reliability"]
    for k, v in rel.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## 7. 失败分布")
    lines.append("")
    lines.append(f"- error_code: {summary['error_code_distribution']}")
    lines.append(f"- failure_stage: {summary['failure_stage_distribution']}")
    lines.append(f"- recommendation: {summary['recommendation_distribution']}")
    lines.append(f"- analysis_completeness: {summary['analysis_completeness_distribution']}")
    lines.append("")
    lines.append("## 8. Paired fast/deep 效率对比")
    lines.append("")
    if paired_rows:
        fast_e2e = [r["fast_e2e_s"] for r in paired_rows if r["fast_e2e_s"] is not None]
        deep_e2e = [r["deep_e2e_s"] for r in paired_rows if r["deep_e2e_s"] is not None]
        avg_change = (
            sum(
                r["e2e_pct_change_fast_vs_deep"]
                for r in paired_rows
                if r["e2e_pct_change_fast_vs_deep"] is not None
            )
            / len([r for r in paired_rows if r["e2e_pct_change_fast_vs_deep"] is not None])
            if any(r["e2e_pct_change_fast_vs_deep"] is not None for r in paired_rows)
            else None
        )
        lines.append(f"- 配对样本数: {len(paired_rows)}")
        lines.append(
            f"- fast e2e avg: {round(sum(fast_e2e) / len(fast_e2e), 3) if fast_e2e else None}s; "
            f"deep e2e avg: {round(sum(deep_e2e) / len(deep_e2e), 3) if deep_e2e else None}s"
        )
        lines.append(f"- e2e avg pct change (fast vs deep): {avg_change}%")
        lines.append(
            "- ⚠️ 只有 claim_candidates.json 满足全部条件时才能输出"
            "'fast 降低 XX% 延迟/Token，质量基本不下降'的候选结论。"
        )
    else:
        lines.append("- 本 suite 无配对数据（仅 smoke/load）。")
    lines.append("")
    lines.append("## 9. Company breakdown")
    lines.append("")
    lines.append(
        "| ticker | sector | jobs | technical | live_acceptance | full_quality | avg_e2e_s |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for row in company_rows:
        lines.append(
            f"| {row['ticker']} | {row['sector']} | {row['jobs']} | "
            f"{row['technical_success']['rate']} "
            f"({row['technical_success']['numerator']}"
            f"/{row['technical_success']['denominator']}) | "
            f"{row['live_acceptance']['rate']} "
            f"({row['live_acceptance']['numerator']}"
            f"/{row['live_acceptance']['denominator']}) | "
            f"{row['full_quality_pass']['rate']} "
            f"({row['full_quality_pass']['numerator']}"
            f"/{row['full_quality_pass']['denominator']}) | "
            f"{row['avg_e2e_s']} |"
        )
    lines.append("")
    lines.append("## 10. 已知限制")
    lines.append("")
    lines.append("- 本脚本只负责评测；未修改业务流程/Prompt/Agent/质量门禁。")
    lines.append("- Prometheus 只能作为聚合旁证；单 job Token 在并发下无法精确归因时写 null。")
    lines.append("- 409 idempotency conflict 无法从响应恢复 job_id（API 契约限制）。")
    lines.append("- 小样本 P95/P99 不稳；smoke 结果不得直接作为最终简历统计。")
    lines.append("- 不执行 docker compose down -v；不删除任何 volume/工件/evals/runs。")
    lines.append("")
    lines.append("## 11. 可以写进简历的事实 vs 证据不足的说法")
    lines.append("")
    lines.append("### 可以写进简历（只有真实测量才可）")
    lines.append("")
    lines.append(
        "1. technical_success_rate / live_acceptance_rate / full_quality_pass_rate "
        "的原始分子、分母与 Wilson 区间。"
    )
    lines.append("2. queue/execution/E2E 的 avg/min/max/P50/P90/P95/P99（并标注小样本不稳定）。")
    lines.append(
        "3. throughput jobs/minute 与每任务/每成功任务 Token、成本"
        "（仅当 usage 完整且 pricing-file 存在）。"
    )
    lines.append("")
    lines.append("### 当前证据不足、不能写进简历")
    lines.append("")
    lines.append(
        "1. 'fast 降低 XX% 延迟/Token，质量基本不下降'——除非 claim_candidates.json "
        "中 claim_eligible=true。"
    )
    lines.append("2. fake workflow_success_rate 作为 live_agent_success_rate——二者口径完全不同。")
    lines.append("3. 任何基于缺失 usage 估算的 Token/费用（缺失必须写 null）。")
    lines.append(
        "4. load suite 中 concurrency=2/4 的能力（Worker concurrency=1 时只测到排队能力）。"
    )
    lines.append("")
    lines.append("## 12. 输出目录")
    lines.append("")
    lines.append(f"- `{run_dir}`")
    return "\n".join(lines)


def _render_annual_mode_report(
    run_dir: Path,
    summary: dict[str, Any],
    paired_rows: list[dict[str, Any]],
    company_rows: list[dict[str, Any]],
    environment: dict[str, Any],
    eligibility: dict[str, Any],
) -> str:
    """年度模式专用报告，明确其证据模型与 legacy 不同且首轮仅为 canary。"""
    lines = [
        "# P07-11 annual_deep / legacy Canary Benchmark Report",
        "",
        f"- benchmark_run_id: `{summary['benchmark_run_id']}`",
        "- 对照：同公司、同 as_of_date、同 deep 档位、仅 10-K；单 Job 串行执行。",
        "- 重要：本轮是 2 家公司 × 2 次的 canary，只能验证评测链路，不可形成简历性能结论。",
        "",
        "## 结果口径",
        "",
        "- legacy 使用既有 workflow_steps 与 00–09 工件判定。",
        "- annual_deep 使用年度节点、Company Facts、Comparison Pack、章节工件及限制说明判定。",
        "- 目标 10-K 或两年 Facts 缺失而阻塞属于安全阻塞，绝不视为成功发布。",
        "",
        "## 模式汇总",
        "",
        "| 模式 | jobs | technical | live acceptance | E2E P50(s) | "
        "年度关键路径 P50(s) | partial | blocked/failed |",
        "|---|---:|---|---|---:|---:|---:|---:|",
    ]
    for mode, stats in (summary.get("mode_comparison") or {}).items():
        technical = stats["technical_success"]
        acceptance = stats["live_acceptance"]
        lines.append(
            f"| {mode} | {stats['jobs']} | {technical['numerator']}/{technical['denominator']} "
            f"({technical['rate']}) | {acceptance['numerator']}/{acceptance['denominator']} "
            f"({acceptance['rate']}) | {stats['e2e_duration']['p50']} | "
            f"{stats['annual_critical_path']['p50']} | {stats['partial_deliveries']} | "
            f"{stats['blocked_or_failed']} |"
        )
    lines.extend(
        [
            "",
            "## 配对对照",
            "",
            f"- 完整 legacy/annual_deep 配对数：{len(paired_rows)}",
            "- 详细配对结果见 `mode_paired_comparison.csv`；"
            "公司拆分见 `mode_company_breakdown.csv`。",
            "",
            "## Token、观测与限制",
            "",
            f"- Token usage 完整率：{summary['token_usage'].get('usage_complete_rate')}；"
            "缺失 usage 始终为 null，不估算 Token 或成本。",
            "- 年度节点和 LLM 指标只使用低基数标签；"
            "不写入 job_id、公司名、Prompt 或原始 SEC 内容。",
            f"- claim_eligible：{eligibility['claim_eligible']}；{eligibility.get('note', '')}",
            "",
            "## 环境",
            "",
        ]
    )
    for key, value in environment.items():
        lines.append(f"- **{key}**: `{value}`")
    lines.extend(["", "## 输出目录", "", f"- `{run_dir}`"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 环境信息
# ---------------------------------------------------------------------------


def collect_environment(settings_summary: dict[str, Any]) -> dict[str, Any]:
    git_commit = ""
    dirty = None
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT, timeout=5
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        git_commit = "unavailable"
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=5,
        ).stdout
        dirty = bool(status.strip())
    except (subprocess.SubprocessError, OSError):
        dirty = None
    try:
        py_version = platform.python_version()
    except Exception:  # noqa: BLE001 - 环境采集尽力而为
        py_version = "unknown"
    info: dict[str, Any] = {
        "time_utc": _now_iso(),
        "timezone": time.tzname,
        "git_commit": git_commit,
        "worktree_dirty": dirty,
        "python_version": py_version,
        "os": f"{platform.system()} {platform.release()}",
        "cpu_logical_cores": os.cpu_count(),
        "provider_model_profile": settings_summary.get("provider_model_profile", {}),
        "prompt_files_sha256": settings_summary.get("prompt_files_sha256", {}),
        "worker_concurrency": settings_summary.get("worker_concurrency", "unknown"),
    }
    try:
        import psutil

        info["memory_total_bytes"] = psutil.virtual_memory().total
        info["cpu_logical_cores"] = psutil.cpu_count(logical=True)
    except Exception:  # noqa: BLE001 - psutil 可选
        info["memory_total_bytes"] = None
    try:
        docker_version = subprocess.run(
            ["docker", "--version"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        compose_version = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        info["docker_version"] = docker_version
        info["compose_version"] = compose_version
    except (subprocess.SubprocessError, OSError):
        info["docker_version"] = "unavailable"
        info["compose_version"] = "unavailable"
    return info


def settings_summary(settings: Any) -> dict[str, Any]:
    """脱敏后的 Settings 摘要（绝不输出密钥）。"""
    from invest_research.agents.llm_factory import LLMConfig, LLMRole

    config = LLMConfig.from_settings(settings)
    models: dict[str, Any] = {}
    for role in (LLMRole.RESEARCH, LLMRole.ANALYSIS, LLMRole.WRITER):
        try:
            role_config = config.config_for(role)
            # 只保留脱敏的模型名/供应商标识，不输出 base_url/api_key。
            models[role.value] = {
                "model": role_config.model,
                "vendor": role_config.vendor,
                "enable_thinking": role_config.enable_thinking,
            }
        except Exception:  # noqa: BLE001 - 环境采集尽力而为，缺失角色不影响主流程
            models[role.value] = None
    provider = getattr(settings, "llm_vendor", None) or getattr(settings, "llm_provider", None)
    return {
        "flow_mode": settings.flow_mode,
        "provider": provider,
        "provider_model_profile": models,
        "worker_concurrency": os.environ.get("CELERY_WORKER_CONCURRENCY", "unknown"),
        "prompt_files_sha256": {},
    }


def prompt_hashes() -> dict[str, str]:
    from invest_research.prompts.loader import PromptName, prompt_sha256

    out: dict[str, str] = {}
    for name in PromptName:
        try:
            out[name.value] = prompt_sha256(name)
        except Exception:  # noqa: BLE001
            out[name.value] = "unavailable"
    return out


# ---------------------------------------------------------------------------
# Runner（提交、轮询、熔断、恢复、Ctrl+C 安全）
# ---------------------------------------------------------------------------

RunOneJobFn = Callable[..., dict[str, Any]]


def _default_run_one(
    api: ApiClient, *, plan: JobPlan, run_id: str, timeout_s: float,
    max_total_tokens: int, token_budget: dict[str, int],
    budget_lock: Optional[threading.Lock] = None,
    judge: Any | None = None,
) -> dict[str, Any]:
    """默认 worker：透传给 run_one_job（便于测试注入）。"""
    return run_one_job(
        api,
        plan=plan,
        run_id=run_id,
        timeout_s=timeout_s,
        max_total_tokens=max_total_tokens,
        token_budget=token_budget,
        budget_lock=budget_lock,
        judge=judge,
    )


class LiveBenchmarkRunner:
    """串行（smoke/paired）或按 concurrency level 有界并发（load）执行计划。

    并发安全约束（修复后）：
    - records / token_budget / failure fuse / partial save 均在锁下更新；
    - 达到 max-failures 或 max-total-tokens 后不再提交新任务，已在途任务允许
      安全收口（完整执行完毕再汇总）；
    - load 每个 level 的 max_workers=configured_concurrency（1/2/4），
      一级完成后再进入下一级，避免污染 fast/deep 配对比对。
    """

    def __init__(
        self,
        *,
        suite: str,
        plans: list[JobPlan],
        api: ApiClient,
        prom: PrometheusClient,
        run_dir: Path,
        timeout_s: float,
        max_failures: int,
        max_total_tokens: int,
        cooldown_s: float,
        resume_existing: set[tuple[Any, ...]],
        summary_config: dict[str, Any],
        run_one_fn: Optional[RunOneJobFn] = None,
        judge: Any | None = None,
    ) -> None:
        self.suite = suite
        self.plans = plans
        self.api = api
        self.prom = prom
        self.run_dir = run_dir
        self.timeout_s = timeout_s
        self.max_failures = max_failures
        self.max_total_tokens = max_total_tokens
        self.cooldown_s = cooldown_s
        self.resume_existing = resume_existing
        self.summary_config = summary_config
        self.judge = judge
        self.run_one_fn = run_one_fn or _default_run_one
        self.records: list[dict[str, Any]] = []
        self.metrics_before: Optional[dict[str, Any]] = None
        self.metrics_after: Optional[dict[str, Any]] = None
        self.token_budget = {"used": 0}
        self._stop = False
        self._lock = threading.Lock()
        self._budget_lock = threading.Lock()

    def run(self) -> int:
        self.metrics_before = self.prom.snapshot()
        run_id = str(self.summary_config["run_id"])

        if self.suite == "load":
            # 按 concurrency level 分组；同一 level 内使用有界线程池并发提交。
            group_by: dict[int, list[JobPlan]] = {}
            for plan in self.plans:
                group_by.setdefault(plan.configured_concurrency, []).append(plan)
            for level in sorted(group_by):
                if self._stop:
                    break
                level_plans = [
                    p for p in group_by[level] if p.identity() not in self.resume_existing
                ]
                skipped = len(group_by[level]) - len(level_plans)
                if skipped:
                    print(f"[resume] concurrency={level}: 跳过 {skipped} 个已存在任务")
                self._run_level(level_plans, max_workers=level, run_id=run_id)
            self.metrics_after = self.prom.snapshot()
            self._save_partial()
            return 0

        # smoke / paired：串行。
        for plan in self.plans:
            if self._stop:
                break
            if plan.identity() in self.resume_existing:
                print(f"[resume] 跳过已存在任务: {plan.case_id}/{plan.profile}/r{plan.repetition}")
                continue
            print(
                f"[{self.suite}] submitting order={plan.execution_order} "
                f"ticker={plan.ticker} profile={plan.profile} rep={plan.repetition} "
                f"concurrency={plan.configured_concurrency}"
            )
            record = self.run_one_fn(
                self.api,
                plan=plan,
                run_id=run_id,
                timeout_s=self.timeout_s,
                max_total_tokens=self.max_total_tokens,
                token_budget=self.token_budget,
                budget_lock=self._budget_lock,
                judge=self.judge,
            )
            with self._lock:
                self.records.append(record)
                self._update_fuse(record)
            if self.cooldown_s > 0:
                time.sleep(self.cooldown_s)
            self._save_partial()
        self.metrics_after = self.prom.snapshot()
        self._save_partial()
        return 0

    def _run_level(
        self, level_plans: list[JobPlan], *, max_workers: int, run_id: str
    ) -> None:
        """在给定 concurrency level 内以有界线程池执行任务。

        线程安全：records / token_budget / fuse 更新都经 self._lock。
        熔断触发后不再向线程池提交新任务；已在执行的任务允许完整收口。
        """
        if not level_plans:
            print(f"[load] concurrency={max_workers}: 无待执行任务")
            return
        print(f"[load] concurrency={max_workers}: 开始 {len(level_plans)} 个任务")
        pending: list[JobPlan] = list(level_plans)
        futures: dict[Any, JobPlan] = {}
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 逐批提交：填满 max_workers 个槽位后等待全部完成，再补下一批。
                # 由于每批大小=max_workers，worker 并发度正好等于 level。
                while pending and not self._stop:
                    batch = pending[:max_workers]
                    del pending[:max_workers]
                    batch_futures = {
                        executor.submit(
                            self.run_one_fn,
                            self.api,
                            plan=plan,
                            run_id=run_id,
                            timeout_s=self.timeout_s,
                            max_total_tokens=self.max_total_tokens,
                            token_budget=self.token_budget,
                            budget_lock=self._budget_lock,
                        ): plan
                        for plan in batch
                    }
                    futures.update(batch_futures)
                    for plan in batch:
                        print(
                            f"[load] submit order={plan.execution_order} "
                            f"ticker={plan.ticker} profile={plan.profile} "
                            f"concurrency={plan.configured_concurrency}"
                        )
                    for fut, plan in batch_futures.items():
                        record = fut.result()
                        with self._lock:
                            self.records.append(record)
                            self._update_fuse(record)
                        futures.pop(fut, None)
                    if self.cooldown_s > 0:
                        time.sleep(self.cooldown_s)
                    self._save_partial()
        finally:
            # Ctrl+C 或异常收口：等待全部在途 future 完成（不丢弃已完成任务）。
            for fut, plan in list(futures.items()):
                record = fut.result()
                with self._lock:
                    self.records.append(record)
                    self._update_fuse(record)
                futures.pop(fut, None)
            self._save_partial()

    def _update_fuse(self, record: dict[str, Any]) -> None:
        """锁内调用：更新失败计数/连续失败与 token 熔断。"""
        if record.get("budget_breached"):
            print("[fuse] max-total-tokens 熔断触发：停止提交新任务（已完成任务保留）")
            self._stop = True
            return
        success = bool(record.get("technical_success"))
        if not success:
            self.token_budget["_failures"] = self.token_budget.get("_failures", 0) + 1
        else:
            self.token_budget["_failures"] = 0
        if self.token_budget.get("_failures", 0) >= self.max_failures:
            print(
                f"[fuse] max-failures={self.max_failures} 达到："
                "停止提交新任务（保存已完成任务和中间汇总）"
            )
            self._stop = True

    def _save_partial(self) -> None:
        """中途/结束时安全保存已完成任务 + 中间汇总（Ctrl+C 后不会丢失）。"""
        with self._lock:
            records_snapshot = list(self.records)
            failures = self.token_budget.get("_failures", 0)
        write_jsonl(self.run_dir / "jobs.jsonl", records_snapshot)
        summary = _build_summary(
            str(self.summary_config["run_id"]),
            self.suite,
            records_snapshot,
            self.summary_config,
            self.prom.delta(self.metrics_before, self.metrics_after)
            if self.metrics_before is not None and self.metrics_after is not None
            else {},
            self.metrics_before,
            self.metrics_after,
        )
        summary["fuse_stopped"] = self._stop
        summary["failure_count"] = failures
        write_json(self.run_dir / "summary_partial.json", summary)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_existing_records(run_dir: Path) -> set[tuple[Any, ...]]:
    """resume 时读取 jobs.jsonl 中已有终态且记录完整的任务（避免重复付费执行）。

    身份与 JobPlan.identity() 对齐：8 元组（含 research_mode）。旧记录缺少模式时按
    legacy 兼容；suite/pair_id/concurrency 缺失仍不会与新版 identity 误碰撞。
    """
    existing: set[tuple[Any, ...]] = set()
    path = run_dir / "jobs.jsonl"
    if not path.exists():
        return existing
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not row.get("terminal_status"):
            continue
        suite = str(row.get("suite") or "")
        case_id = row.get("case_id")
        profile = row.get("profile")
        research_mode = str(row.get("research_mode") or "legacy")
        ticker = row.get("ticker")
        repetition = row.get("repetition")
        pair_id = str(row.get("pair_id") or "")
        try:
            concurrency = int(row.get("configured_concurrency") or 1)
        except (TypeError, ValueError):
            concurrency = 1
        if (
            row.get("error_code") == "CLIENT_SUBMIT_ERROR"
            or row.get("failure_stage") == "client_submit"
        ):
            continue
        if case_id and profile and ticker and repetition is not None:
            identity: tuple[Any, ...] = (
                suite,
                str(case_id),
                str(profile),
                str(ticker),
                int(repetition),
                pair_id,
                concurrency,
            )
            if research_mode != "legacy":
                identity = (*identity[:3], research_mode, *identity[3:])
            existing.add(identity)
    return existing


def load_resume_run(
    run_dir: Path,
) -> tuple[list[dict[str, Any]], set[tuple[Any, ...]]]:
    records: list[dict[str, Any]] = []
    path = run_dir / "jobs.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records, read_existing_records(run_dir)


def _record_identity(record: dict[str, Any]) -> tuple[Any, ...]:
    """与 JobPlan.identity 保持兼容，用于 resume 后以新成功记录替换客户端伪失败。"""
    base: tuple[Any, ...] = (
        str(record.get("suite") or ""),
        str(record.get("case_id") or ""),
        str(record.get("profile") or ""),
        str(record.get("ticker") or ""),
        int(record.get("repetition") or 0),
        str(record.get("pair_id") or ""),
        int(record.get("configured_concurrency") or 1),
    )
    mode = str(record.get("research_mode") or "legacy")
    return (*base[:3], mode, *base[3:]) if mode != "legacy" else base


def merge_records(
    existing: Sequence[dict[str, Any]], new: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """同一身份只保留最新记录，令 resume 能以已恢复 job 替换客户端提交伪失败。"""
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record in (*existing, *new):
        merged[_record_identity(record)] = dict(record)
    return list(merged.values())


# ---------------------------------------------------------------------------
# preflight / CLI
# ---------------------------------------------------------------------------


def preflight_live(api: ApiClient, prom: PrometheusClient) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not api.health():
        errors.append("API /health 不可达")
    ok, detail = api.readiness()
    if not ok:
        errors.append(f"API /readiness 未就绪: {detail}")
    if not prom.healthy():
        errors.append("Prometheus /-/healthy 不可达")
    if errors:
        return False, errors
    return True, []


def load_settings() -> Any:
    from invest_research.settings import Settings

    return Settings(_env_file=DEFAULT_ENV_FILE)


def validate_live_environment(settings: Any) -> tuple[bool, list[str]]:
    """真正运行前的 Settings 校验（FLOW_MODE=live + 非占位符密钥）。"""
    errors: list[str] = []
    if getattr(settings, "flow_mode", None) != "live":
        errors.append(f"FLOW_MODE 必须是 live（当前 {getattr(settings, 'flow_mode', None)!r}）")
    llm_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    if is_placeholder_secret(llm_key):
        errors.append("LLM_API_KEY 缺失/占位符（拒绝运行 live benchmark）")
    serper = settings.serper_api_key.get_secret_value() if settings.serper_api_key else ""
    if is_placeholder_secret(serper):
        errors.append("SERPER_API_KEY 缺失/占位符（拒绝运行 live benchmark）")
    return (len(errors) == 0, errors)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P06-11 live portfolio benchmark（默认 dry-run，绝不产生付费调用）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--suite", choices=["smoke", "paired", "load", "annual-paired"], default="smoke"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="显式 dry-run（默认无 --confirm-live 即 dry-run）"
    )
    parser.add_argument(
        "--confirm-live", action="store_true", help="确认真正执行（联网、可能产生真实费用）"
    )
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--prometheus-base", default="http://localhost:9090")
    parser.add_argument("--repeats", type=int, default=1, help="paired 重复次数（正式推荐 3）")
    parser.add_argument("--concurrency", type=int, default=1, help="提交并发（paired 固定 1）")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--profile", choices=PROFILES, default="fast", help="load suite 档位")
    parser.add_argument("--concurrency-levels", type=_parse_int_list, default=[1, 2, 4])
    parser.add_argument("--jobs-per-level", type=int, default=8)
    parser.add_argument("--max-jobs", type=int, default=0, help="0=不限制；超过则停止提交新任务")
    parser.add_argument("--max-failures", type=int, default=DEFAULT_MAX_FAILURES)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--cooldown-seconds", type=float, default=0.0)
    parser.add_argument("--max-total-tokens", type=int, default=DEFAULT_MAX_TOTAL_TOKENS)
    parser.add_argument("--pricing-file", type=Path, default=None)
    parser.add_argument(
        "--judge",
        action="store_true",
        help=(
            "对报告做 LLM-as-judge 软评分（可读性/连贯/信息密度；"
            "仅 confirm-live，regrade 永远免费）"
        ),
    )
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--resume", default="", help="resume 已存在的 run_id（跳过已终态且记录完整的任务）"
    )
    parser.add_argument(
        "--regrade-existing", default="",
        help="只读重评分既有 run_id（绝不 POST 新任务；备份 jobs.pre_regrade.jsonl）",
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--output-dir", type=Path, default=None, help="覆盖默认 evals/live_runs/<run_id>"
    )
    return parser


def _parse_int_list(value: str) -> list[int]:
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("empty list")
    return [int(p) for p in parts]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.suite == "annual-paired" and args.dataset == DEFAULT_DATASET:
        args.dataset = DEFAULT_ANNUAL_DATASET

    # 1. 数据集校验（dry-run 也执行，确保配置正确）。
    cases = load_live_dataset(args.dataset)
    dataset_errors = validate_dataset(cases, annual_only=args.suite == "annual-paired")
    if dataset_errors:
        print("数据集校验失败：")
        for err in dataset_errors:
            print(f"  - {err}")
        return 3

    # 2. 生成计划（dry-run 也执行，输出将提交的任务）。
    plans = build_plan(args.suite, cases, args)
    print(f"[{args.suite}] 计划任务数: {len(plans)}")
    if args.max_jobs > 0:
        plans = plans[: args.max_jobs]
        print(f"[max-jobs] 裁剪为 {len(plans)} 个")

    run_id = _safe_name(args.resume or args.run_id or uuid.uuid4().hex[:12])
    runs_dir = ANNUAL_BENCHMARK_RUNS_DIR if args.suite == "annual-paired" else LIVE_RUNS_DIR
    output_dir = args.output_dir or (runs_dir / run_id)

    config = {
        "suite": args.suite,
        "repeats": args.repeats,
        "concurrency": args.concurrency,
        "seed": args.seed,
        "profile": args.profile,
        "research_mode_comparison": (
            ["legacy", "annual_deep"] if args.suite == "annual-paired" else None
        ),
        "concurrency_levels": args.concurrency_levels,
        "jobs_per_level": args.jobs_per_level,
        "max_jobs": args.max_jobs,
        "max_failures": args.max_failures,
        "timeout_seconds": args.timeout_seconds,
        "cooldown_seconds": args.cooldown_seconds,
        "max_total_tokens": args.max_total_tokens,
        "pricing_file": str(args.pricing_file) if args.pricing_file else None,
        "api_base": args.api_base,
        "prometheus_base": args.prometheus_base,
        "run_id": run_id,
        "dataset": str(args.dataset),
        "resume": args.resume,
    }

    # 2.5 免费 regrade（只读：GET job + 下载工件，绝不 POST 新任务）。
    # 不要求 --confirm-live、不做 Settings 校验、不查询 Prometheus、不 create_job。
    if args.regrade_existing:
        out_dir = runs_dir / _safe_name(args.regrade_existing)
        api = ApiClient(args.api_base, timeout_s=args.timeout_seconds)
        return regrade_existing(args.regrade_existing, api, out_dir)

    # 3. 默认 dry-run（无 --confirm-live 绝不联网）。
    confirm_live = bool(args.confirm_live) and not args.dry_run
    if not confirm_live:
        print(
            "[dry-run] 未提供 --confirm-live（或显式 --dry-run）："
            "不联网、不调用 SEC/Serper/LLM、不写运行目录。"
        )
        print(f"[dry-run] 将使用 run_id={run_id}，输出目录 {output_dir}")
        print("[dry-run] 将提交以下任务（不执行）：")
        for p in plans:
            print(
                f"  - {p.suite}/{p.case_id}/{p.profile}/{p.research_mode}/r{p.repetition} "
                f"concurrency={p.configured_concurrency} key={idempotency_key(run_id, p)}"
            )
        return 0

    # 4. 真正运行前的环境校验。
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 - 配置加载失败转为可读错误
        print(f"Settings 加载失败: {type(exc).__name__}（请检查根目录 .env）")
        return 4
    env_ok, env_errors = validate_live_environment(settings)
    if not env_ok:
        print("live 环境校验失败：")
        for err in env_errors:
            print(f"  - {err}")
        return 5

    # 5. HTTP 预检。
    api = ApiClient(args.api_base, timeout_s=args.timeout_seconds)
    prom = PrometheusClient(args.prometheus_base)
    ok, errors = preflight_live(api, prom)
    if not ok:
        print("preflight 失败（fail-fast，Prometheus 不可达时绝不伪装成功）：")
        for err in errors:
            print(f"  - {err}")
        return 6

    # 6. resume：读取既有记录。
    existing_records: list[dict[str, Any]] = []
    resume_existing: set[tuple[Any, ...]] = set()
    if args.resume:
        existing_records, resume_existing = load_resume_run(output_dir)

    # 7. 收集环境信息（脱敏）。
    env = collect_environment(settings_summary(settings))
    env["prompt_files_sha256"] = prompt_hashes()

    # 8. 执行。LLM-as-judge 仅 confirm-live + --judge 才构建（regrade 永远免费）。
    judge = None
    if bool(args.judge) and confirm_live:

        def _build_judge_completion() -> Any:
            from invest_research.agents.llm_factory import LLMConfig
            from invest_research.infrastructure.annual_llm_writing import AnnualLlmDispatcher

            return AnnualLlmDispatcher(LLMConfig.from_settings(settings))

        from scripts.llm_judge import LLMJudge

        judge = LLMJudge(build_completion=_build_judge_completion)

    runner = LiveBenchmarkRunner(
        suite=args.suite,
        plans=plans,
        api=api,
        prom=prom,
        run_dir=output_dir,
        timeout_s=args.timeout_seconds,
        max_failures=args.max_failures,
        max_total_tokens=args.max_total_tokens,
        cooldown_s=args.cooldown_seconds,
        resume_existing=resume_existing,
        summary_config={**config, "run_id": run_id},
        judge=judge,
    )
    try:
        runner.run()
    except KeyboardInterrupt:
        print("\n[interrupt] Ctrl+C 收到：安全保存已完成结果与中间汇总")
        with runner._lock:
            runner._stop = True
        runner.metrics_after = prom.snapshot()
        runner._save_partial()

    records = merge_records(existing_records, runner.records)
    metrics_diff = (
        prom.delta(runner.metrics_before, runner.metrics_after)
        if (runner.metrics_before is not None and runner.metrics_after is not None)
        else {}
    )
    resets = prom.detect_counter_resets(metrics_diff)

    summary = _build_summary(
        run_id,
        args.suite,
        records,
        config,
        metrics_diff,
        runner.metrics_before,
        runner.metrics_after,
    )
    if resets:
        summary["prometheus_counter_resets"] = resets
        print(f"[warn] 检测到 {len(resets)} 个 Prometheus Counter 重置（不作为本轮结果）：")
        for r in resets:
            print(f"  - {r}")

    # 9. 输出所有产物。
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "config.json", config)
    write_json(output_dir / "environment.json", env)
    write_json(
        output_dir / "dataset_snapshot.json", {"schema_version": "live_dataset_v1", "cases": cases}
    )
    write_jsonl(output_dir / "jobs.jsonl", records)
    failures = [r for r in records if not r.get("technical_success")]
    write_jsonl(output_dir / "failures.jsonl", failures)
    write_json(output_dir / "metrics_snapshot_before.json", runner.metrics_before or {})
    write_json(output_dir / "metrics_snapshot_after.json", runner.metrics_after or {})
    write_json(output_dir / "metrics_diff.json", metrics_diff)
    write_json(output_dir / "summary.json", summary)

    paired_rows = (
        _annual_mode_paired_comparison(records)
        if args.suite == "annual-paired"
        else _paired_comparison(records)
    )
    paired_name = (
        "mode_paired_comparison.csv" if args.suite == "annual-paired" else "paired_comparison.csv"
    )
    _write_csv(output_dir / paired_name, paired_rows)
    company_rows = _company_breakdown(records)
    company_name = (
        "mode_company_breakdown.csv" if args.suite == "annual-paired" else "company_breakdown.csv"
    )
    _write_csv(output_dir / company_name, _flatten_company_rows(company_rows))

    # 10. 结论资格（claim_candidates）。
    eligibility = (
        _annual_claim_eligibility(records, repeats=args.repeats)
        if args.suite == "annual-paired"
        else _claim_eligibility(records, repeats=args.repeats)
    )
    claims = [
        {
            "claim": (
                "annual_deep 相对 legacy 的耗时与质量观察（首轮 canary，不可用于简历性能结论）"
                if args.suite == "annual-paired"
                else "fast 相对 deep 降低 X% 延迟/Token，质量基本不下降"
            ),
            "eligible": eligibility["claim_eligible"],
            "evidence": eligibility,
            "required_conditions": eligibility["conditions"],
        }
    ]
    write_json(output_dir / "claim_candidates.json", claims)

    report = _render_report(
        output_dir, summary, records, paired_rows, company_rows, env, eligibility
    )
    (output_dir / "report.md").write_text(report, encoding="utf-8")

    # 11. 控制台摘要。
    print("\n===== 结果摘要 =====")
    for name, stats in summary["success_rates"].items():
        print(
            f"{name}: {stats['numerator']}/{stats['denominator']} "
            f"(rate={stats['rate']} wilson=[{stats['wilson_low']},{stats['wilson_high']}])"
        )
    print(f"claim_eligible: {eligibility['claim_eligible']}")
    print(f"输出目录: {output_dir}")
    print("注意：本工具不修改业务/Prompt/Agent；P06-11 在真实 paired 完成前保持未标记 ✅。")
    return 0


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _flatten_company_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        flat = dict(row)
        for key in ("technical_success", "live_acceptance", "full_quality_pass"):
            stats = row.get(key) or {}
            flat[f"{key}_rate"] = stats.get("rate")
            flat[f"{key}_numerator"] = stats.get("numerator")
            flat[f"{key}_denominator"] = stats.get("denominator")
            flat.pop(key, None)
        out.append(flat)
    return out




# ---------------------------------------------------------------------------
# P06-11 免费 regrade（只读重评分既有 run，绝不 POST 新任务/发起付费调用）
# ---------------------------------------------------------------------------


def regrade_existing(run_id: str, api: ApiClient, output_dir: Path) -> int:
    """只读重评分：读取已有 jobs.jsonl -> GET job + 下载工件 -> 重新评测。

    - 绝不调用 create_job（测试断言 create_job 调用次数=0）；
    - 原始记录幂等备份为 jobs.pre_regrade.jsonl；
    - 新记录写入 evaluator_version / regraded_at；
    - 重新生成 summary/report/claim_candidates/company_breakdown/failures。
    """
    jobs_path = output_dir / "jobs.jsonl"
    if not jobs_path.exists():
        print(f"[regrade] {run_id} 不存在 jobs.jsonl")
        return 7
    original = load_resume_run(output_dir)[0]
    backup = output_dir / "jobs.pre_regrade.jsonl"
    if not backup.exists():
        backup.write_text(jobs_path.read_text(encoding="utf-8"), encoding="utf-8")

    regraded: list[dict[str, Any]] = []
    for row in original:
        new = dict(row)
        job_id = str(row.get("job_id") or "")
        if job_id:
            sc, job = api.get_job(job_id)
            artifacts: dict[str, bytes] = {}
            if sc == 200:
                for key in REQUIRED_ARTIFACT_KEYS:
                    code, content = api.download_artifact(job_id, key)
                    if code == 200:
                        artifacts[key] = content
                new = _regrade_record(new, job, artifacts)
        new["evaluator_version"] = EVALUATOR_VERSION
        new["regraded_at"] = _now_iso()
        regraded.append(new)

    write_jsonl(jobs_path, regraded)
    config = json.loads((output_dir / "config.json").read_text(encoding="utf-8"))
    suite = config.get("suite", "smoke")
    repeats = int(config.get("repeats", 1))
    summary = _build_summary(run_id, suite, regraded, config, {}, None, None)
    write_json(output_dir / "summary.json", summary)
    paired_rows = _paired_comparison(regraded)
    _write_csv(output_dir / "paired_comparison.csv", paired_rows)
    company_rows = _company_breakdown(regraded)
    _write_csv(output_dir / "company_breakdown.csv", _flatten_company_rows(company_rows))
    eligibility = _claim_eligibility(regraded, repeats=repeats)
    claims = [{
        "claim": "fast 相对 deep 降低 X% 延迟/Token，质量基本不下降",
        "eligible": eligibility["claim_eligible"],
        "evidence": eligibility,
        "required_conditions": eligibility["conditions"],
    }]
    write_json(output_dir / "claim_candidates.json", claims)
    failures = [r for r in regraded if not r.get("technical_success")]
    write_jsonl(output_dir / "failures.jsonl", failures)
    report = _render_report(output_dir, summary, regraded, paired_rows, company_rows,
                            {}, eligibility)
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"[regrade] {len(regraded)} 条记录已只读重评分（无任何新任务/费用）")
    return 0


def _regrade_record(row: dict[str, Any], job: dict[str, Any],
                    artifacts: dict[str, bytes]) -> dict[str, Any]:
    """用修复后的 evaluator 对单条记录重新评分（复用 run_one_job 的判定逻辑）。"""
    new = dict(row)
    for key in (
        "evaluate_technical_failures",
        "evaluate_live_acceptance_failures",
        "evaluate_full_quality_failures",
    ):
        new.pop(key, None)
    new.update(_job_to_record(job))
    quality_report = _json_bytes(artifacts.get(QUALITY_ARTIFACT))
    manifest = _json_bytes(artifacts.get(MANIFEST_ARTIFACT))
    request_artifact = _json_bytes(artifacts.get("00_request.json"))
    research_pack = _json_bytes(artifacts.get(RESEARCH_PACK_ARTIFACT))
    report_draft = _json_bytes(artifacts.get(REPORT_DRAFT_ARTIFACT))
    analysis_pack = _json_bytes(artifacts.get(ANALYSIS_PACK_ARTIFACT))
    # as_of_date 回退顺序（兼容旧 jobs.jsonl 缺少该字段）：
    # 1) row.as_of_date -> 2) 00_request.json -> 3) 07_manifest.json；
    # 无法取得才为 None。找到后写回 new["as_of_date"] 供后续判定使用。
    as_of = new.get("as_of_date")
    if not as_of and isinstance(request_artifact, dict):
        as_of = request_artifact.get("as_of_date")
    if not as_of and isinstance(manifest, dict):
        as_of = manifest.get("as_of_date")
    as_of = str(as_of) if as_of else ""
    new["as_of_date"] = as_of or None
    new["quality_recommendation"] = (quality_report or {}).get("recommendation")
    new["quality_all_passed"] = (quality_report or {}).get("all_passed")
    new["analysis_completeness"] = (analysis_pack or {}).get("completeness")
    cit = _count_citations(report_draft)
    new.update(cit)
    new["invalid_citation_count"] = cit["citation_count"] - cit["valid_citation_count"]
    sec_stats = _map_citations_to_sec_sources(research_pack, report_draft)
    new["official_sec_source_count"] = sec_stats["official_sec_source_count"]
    new["has_official_sec_source"] = sec_stats["has_official_sec_source"]
    new["official_sec_citation_coverage"] = sec_stats["official_sec_citation_coverage"]
    new["as_of_compliant"] = _as_of_compliance(research_pack, as_of)
    report_md = artifacts.get(REPORT_MD_ARTIFACT, b"").decode("utf-8", errors="replace")
    _fidelity_checks(new, report_md, research_pack, report_draft, analysis_pack)
    new["required_artifacts"] = sorted(artifacts.keys())
    new["missing_artifacts"] = sorted(set(REQUIRED_ARTIFACT_KEYS) - set(artifacts))
    perf = manifest.get("performance") if isinstance(manifest, dict) else None
    usage = None
    if isinstance(perf, dict):
        usage = perf.get("token_usage") if isinstance(perf.get("token_usage"), dict) else None
    if isinstance(usage, dict):
        new["input_tokens"] = usage.get("prompt_tokens")
        new["output_tokens"] = usage.get("completion_tokens")
        new["cached_input_tokens"] = usage.get("cached_prompt_tokens")
        new["total_tokens"] = usage.get("total_tokens")
        new["token_usage_complete"] = (
            isinstance(new["input_tokens"], (int, float))
            and isinstance(new["output_tokens"], (int, float))
            and isinstance(new["total_tokens"], (int, float))
        )
    else:
        new["token_usage_complete"] = False
        new["input_tokens"] = None
        new["output_tokens"] = None
        new["cached_input_tokens"] = None
        new["total_tokens"] = None
    tech_ok, tech_failures = evaluate_technical_success(
        job, artifacts=artifacts, timeout_hit=bool(row.get("timeout")))
    new["technical_success"] = bool(tech_ok)
    new["live_acceptance_success"] = False
    new["full_quality_success"] = False
    if tech_ok:
        live_ok, live_failures = evaluate_live_acceptance(
            job, quality_report=quality_report, manifest=manifest,
            research_pack=research_pack, report_draft=report_draft,
            as_of_date=as_of, artifacts=artifacts)
        if live_ok:
            new["live_acceptance_success"] = True
            full_ok, full_failures = evaluate_full_quality(True, quality_report, analysis_pack)
            if full_ok:
                new["full_quality_success"] = True
            else:
                new["evaluate_full_quality_failures"] = full_failures
        else:
            new["evaluate_live_acceptance_failures"] = live_failures
    else:
        new["evaluate_technical_failures"] = tech_failures
    new["success_checks"] = {
        "technical_success": bool(new.get("technical_success")),
        "live_acceptance_success": bool(new.get("live_acceptance_success")),
        "full_quality_success": bool(new.get("full_quality_success")),
    }
    return new
if __name__ == "__main__":
    sys.exit(main())

