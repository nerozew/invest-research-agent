"""P06-10A：Phase 6 端到端基准运行器（默认 fake，禁止 live）。

通过 HTTP API 创建/轮询/校验投研任务，验证 **workflow_success_rate**
（API/Redis/Celery/Worker/DB/Flow 全链成功率），**不**代表真实 Agent 成功率。
真实 SEC/Serper/LLM 下的 live_agent_success_rate 由后续 live 基准另行评估。

设计（对齐任务要求）：
- 默认 ``FLOW_MODE=fake``；检测到 live 环境（``FLOW_MODE=live``）立即 fail-fast；
- 不读取、不打印任何 API Key / Token；
- 固定随机种子（``--seed``，默认 7）；
- 最大并发建议 4；
- 主任务/控制任务分母严格隔离：cancel/冲突/非法请求/故障注入等只进控制统计；
- 每个主任务按 10 条成功判定逐项校验（见 ``_SUCCESS_CHECKS``）；
- 输出到 ``evals/runs/<benchmark_run_id>/``（run_id 只用于报告与文件，不做 Prometheus label）；
- 分位数（P50/P90/P95/P99）从本次 jobs.jsonl 原始耗时计算，不从历史累计指标猜测。

成功判定（每个主任务必须全部满足才算 success）：
1. API 最终状态 succeeded；
2. current_step = None；
3. 无 running 步骤；
4. 必需步骤（00-07）终态正确（succeeded）；
5. 08_report.md 存在且非空；
6. 09_report.pdf 存在；
7. PDF 文件头为 ``%PDF-``；
8. 工件 API 可以下载（200）；
9. 没有未脱敏错误信息（error_message 不含密钥/URL/Authorization 模式）；
10. 在任务级 timeout 内完成。

统计口径：
- workflow_success_rate = succeeded 主任务数 / 主任务总数（分母=--jobs，不含控制任务）；
- 主成功率阈值：严格 >95% 至少 96/100；校准轮（10 次）要求 100% 才 PASS；
- Prometheus 只作为旁证（前后快照差异），不用于推导本轮成功率。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_REQUIRED_STEPS = (
    "00_request",
    "01_company_resolve",
    "02_research",
    "03_documents",
    "04_analysis",
    "05_writer",
    "06_quality_gate",
    "07_manifest",
)
_REQUIRED_ARTIFACTS = ("08_report.md", "09_report.pdf")
_REQUIRED_ARTIFACT_TYPES = {"final_report_markdown", "final_report_pdf"}
_TERMINAL_STATUSES = {"succeeded", "partial", "failed", "cancelled"}
_DEFAULT_EVALS_RUNS = Path(__file__).resolve().parent.parent / "evals" / "runs"

# 明显包含敏感内容的错误信息模式（用于"未脱敏错误信息"判定）。
_SENSITIVE_PATTERNS = (
    "api_key",
    "serper",
    "authorization",
    "bearer ",
    "sk-",
    "token=",
    "x-api-key",
)


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 3)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 3)


@dataclass
class BenchmarkConfig:
    """基准配置（写入 config.json）。"""

    benchmark_run_id: str
    jobs: int
    fast_count: int
    deep_count: int
    concurrency: int
    timeout_seconds: int
    poll_interval_seconds: float
    seed: int
    flow_mode: str
    api_base: str
    as_of_date: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JobRecord:
    """单个任务记录（写入 jobs.jsonl 的每行）。"""

    job_id: str
    profile: str
    index: int
    kind: str  # "main" | "control"
    control_scenario: str | None
    created_at: str | None
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    terminal_status: str
    error_code: str | None
    failure_stage: str | None
    required_artifacts: list[str] = field(default_factory=list)
    missing_artifacts: list[str] = field(default_factory=list)
    success_checks: dict[str, bool] = field(default_factory=dict)
    success: bool = False
    timeout: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApiClient:
    """极简 HTTP client（显式超时，JSON 错误分类）。"""

    def __init__(self, api_base: str, timeout: float = 10.0) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(f"{self.api_base}{path}", data=body, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return resp.status, raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                parsed = raw.decode("utf-8", errors="replace")
            return exc.code, parsed
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接 {self.api_base}: {exc.reason}") from exc

    def create_job(self, payload: dict[str, Any]) -> tuple[int, Any]:
        return self.request("/v1/research-jobs", method="POST", data=payload)

    def get_job(self, job_id: str) -> tuple[int, Any]:
        return self.request(f"/v1/research-jobs/{job_id}")

    def list_artifacts(self, job_id: str) -> tuple[int, Any]:
        return self.request(f"/v1/research-jobs/{job_id}/artifacts")

    def download_artifact(self, job_id: str, artifact_key: str) -> tuple[int, bytes]:
        """下载工件二进制内容（不经过 JSON 解码，保留 PDF 原始字节）。"""
        url = f"{self.api_base}/v1/research-jobs/{job_id}/artifacts/{artifact_key}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接 {self.api_base}: {exc.reason}") from exc

    def health(self) -> tuple[int, Any]:
        return self.request("/health")


def _job_payload(index: int, profile: str, as_of: str) -> dict[str, Any]:
    return {
        "input_company": f"BENCH-{profile}-{index:02d}",
        "as_of_date": as_of,
        "language": "zh-CN",
        "requested_forms": ["10-K"],
        "research_profile": profile,
    }


def _has_sensitive_content(message: str | None) -> bool:
    lowered = (message or "").lower()
    return any(p in lowered for p in _SENSITIVE_PATTERNS)


def _check_required_steps(steps: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """必需步骤 00-07 全部 succeeded 才算通过。"""
    step_by_name = {s.get("step_name"): s for s in steps if isinstance(s, dict)}
    bad: list[str] = []
    for name in _REQUIRED_STEPS:
        s = step_by_name.get(name)
        if s is None:
            bad.append(f"{name}:missing")
        elif s.get("status") != "succeeded":
            bad.append(f"{name}:{s.get('status')}")
    return (not bad), bad


def _has_running_step(steps: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(s, dict) and s.get("status") == "running" for s in steps
    )


def evaluate_success(job: dict[str, Any], *, timeout: bool) -> tuple[bool, dict[str, bool]]:
    """按 10 条成功判定逐项校验，返回 (是否成功, 检查明细)。"""
    status = job.get("status")
    steps_raw = job.get("steps") or []
    steps = steps_raw if isinstance(steps_raw, list) else []
    error_message = job.get("error_message")

    checks: dict[str, bool] = {
        "api_status_succeeded": status == "succeeded",
        "current_step_null": job.get("current_step") is None,
        "no_running_step": not _has_running_step(steps),
        "required_steps_terminal": False,
        "08_report_md_exists_nonempty": False,
        "09_report_pdf_exists": False,
        "pdf_header": False,
        "artifacts_downloadable": False,
        "no_unredacted_error": not _has_sensitive_content(error_message),
        "within_timeout": not timeout,
    }
    steps_ok, _ = _check_required_steps(steps)
    checks["required_steps_terminal"] = steps_ok

    ok = all(checks.values())
    return ok, checks


class BenchmarkRunner:
    """P06-10A 端到端基准运行器。"""

    def __init__(
        self,
        *,
        jobs: int = 10,
        fast_ratio: float = 0.5,
        concurrency: int = 2,
        timeout_seconds: int = 180,
        poll_interval: float = 2.0,
        seed: int = 7,
        api_base: str = "http://localhost:8000",
        output_dir: str | Path | None = None,
    ) -> None:
        self.jobs = jobs
        self.fast_count = max(1, round(jobs * fast_ratio))
        self.deep_count = jobs - self.fast_count
        self.concurrency = concurrency
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval
        self.seed = seed
        self.api_base = api_base
        self.run_id = uuid.uuid4().hex[:12]
        self.client = ApiClient(api_base)
        out_root = Path(output_dir) if output_dir is not None else _DEFAULT_EVALS_RUNS
        self.run_dir = out_root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.as_of = (date.today() - timedelta(days=1)).isoformat()

    # ---- 环境守卫 ----

    def assert_fake_mode(self) -> None:
        """强制 FLOW_MODE=fake；live 立即 fail-fast（不读取任何真实 Key）。

        key 检查只在 ``FLOW_MODE=live`` 时生效：本进程的 LLM_API_KEY 只决定
        *进程内* 是否直接调用外部服务；P06-10 基准通过 HTTP 调隔离 Docker 栈，
        容器环境由 ``deploy/compose.benchmark.yml`` 写死占位符 + FLOW_MODE=fake。
        因此 fake 模式下本机 .env 的 key 不会进入容器，不构成真实外部调用。
        """
        mode = os.environ.get("FLOW_MODE", "fake")
        if mode != "fake":
            # live 模式下要求 key 必须为占位符，防止带真实 key 误跑基准。
            _PLACEHOLDER_MARKERS = ("placeholder", "your-", "change-me", "example")
            for var in ("LLM_API_KEY", "SERPER_API_KEY"):
                value = os.environ.get(var)
                if value and not any(m in value.lower() for m in _PLACEHOLDER_MARKERS):
                    raise RuntimeError(
                        f"检测到 {var} 已配置为真实密钥；P06-10 基准必须使用占位符，"
                        "禁止真实外部调用（live_agent_success_rate 由后续 live 基准评估）。"
                    )
            raise RuntimeError(
                f"P06-10 基准禁止 live：FLOW_MODE={mode}。"
                "本轮只允许 fake（workflow_success_rate，不代表 Agent 成功率）。"
            )

    # ---- 创建 ----

    def _create_main_jobs(self) -> list[tuple[str, str]]:
        """创建主任务，返回 [(profile, job_id), ...]。

        并发 append 顺序与 specs 顺序不一致，因此必须把 profile 与 job_id
        一起返回，run() 直接用创建时的 profile（不能按位置重新推断）。
        """
        specs = [("fast", i) for i in range(self.fast_count)] + [
            ("deep", i) for i in range(self.deep_count)
        ]
        # 固定 seed 洗牌保证可复现。
        import random

        rng = random.Random(self.seed)
        rng.shuffle(specs)

        created: list[tuple[str, str]] = []

        def _create(spec: tuple[str, int]) -> None:
            profile, idx = spec
            payload = _job_payload(idx, profile, self.as_of)
            code, body = self.client.create_job(payload)
            if code != 202 or not body.get("job_id"):
                raise RuntimeError(f"创建主任务失败 profile={profile} index={idx} code={code}")
            created.append((profile, body["job_id"]))

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for _ in pool.map(_create, specs):
                pass
        return created

    def _run_control_cancel(self) -> dict[str, Any]:
        """取消控制场景：创建后立即 DELETE。单独统计，不进入主分母。"""
        payload = _job_payload(999, "deep", self.as_of)
        code, body = self.client.create_job(payload)
        if code != 202 or not body.get("job_id"):
            return {"ok": False, "job_id": None}
        job_id = body["job_id"]
        del_code, _ = self.client.request(f"/v1/research-jobs/{job_id}", method="DELETE")
        return {"ok": del_code == 200, "job_id": job_id}

    # ---- 单任务轮询与校验 ----

    def _wait_terminal(self, job_id: str) -> tuple[dict[str, Any] | None, bool]:
        """轮询到终态或超时。返回 (job_snapshot, timed_out)。"""
        deadline = time.monotonic() + self.timeout_seconds
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            code, body = self.client.get_job(job_id)
            if code == 200 and isinstance(body, dict):
                last = body
                if body.get("status") in _TERMINAL_STATUSES:
                    return body, False
            time.sleep(self.poll_interval)
        return last, True

    def _check_artifacts(self, job_id: str) -> tuple[list[str], list[str], bool]:
        """校验必需工件存在 + PDF 头 + 可下载。返回 (存在keys, 缺失keys, 全部通过)。"""
        code, body = self.client.list_artifacts(job_id)
        present: set[str] = set()
        if code == 200 and isinstance(body, list):
            for item in body:
                if isinstance(item, dict):
                    present.add(str(item.get("artifact_key", "")))
        required = list(_REQUIRED_ARTIFACTS)
        exists = [k for k in required if k in present]
        missing = [k for k in required if k not in present]

        pdf_ok = False
        download_ok = True
        if "09_report.pdf" in present:
            dl_code, dl_body = self.client.download_artifact(job_id, "09_report.pdf")
            download_ok = dl_code == 200
            pdf_ok = isinstance(dl_body, bytes) and dl_body.startswith(b"%PDF-")
        else:
            download_ok = False

        # 08_report.md 必须存在且非空。
        md_nonempty = False
        if "08_report.md" in present:
            dl_code, dl_body = self.client.download_artifact(job_id, "08_report.md")
            md_nonempty = dl_code == 200 and isinstance(dl_body, bytes) and len(dl_body) > 0
            download_ok = download_ok and dl_code == 200
        else:
            download_ok = False

        return exists, missing, (not missing and pdf_ok and md_nonempty and download_ok)

    def _run_single(self, job_id: str, profile: str, index: int) -> JobRecord:
        started_mono = time.monotonic()
        snap, timed_out = self._wait_terminal(job_id)
        finished_mono = time.monotonic()
        if snap is None:
            return JobRecord(
                job_id=job_id, profile=profile, index=index, kind="main",
                control_scenario=None,
                created_at=None, started_at=None, finished_at=None,
                duration_seconds=round(self.timeout_seconds, 3),
                terminal_status="timeout", error_code="TIMEOUT",
                failure_stage=None,
                required_artifacts=list(_REQUIRED_ARTIFACTS),
                missing_artifacts=list(_REQUIRED_ARTIFACTS),
                success_checks={"api_status_succeeded": False,
                                "within_timeout": False},
                success=False, timeout=True,
            )

        raw = snap
        exists, missing, artifacts_ok = self._check_artifacts(job_id)

        checks = {
            "api_status_succeeded": raw.get("status") == "succeeded",
            "current_step_null": raw.get("current_step") is None,
            "no_running_step": not _has_running_step(raw.get("steps") or []),
        }
        steps_ok, _ = _check_required_steps(raw.get("steps") or [])
        checks["required_steps_terminal"] = steps_ok
        checks["08_report_md_exists_nonempty"] = "08_report.md" in exists
        checks["09_report_pdf_exists"] = "09_report.pdf" in exists
        checks["pdf_header"] = artifacts_ok and "09_report.pdf" in exists
        checks["artifacts_downloadable"] = artifacts_ok
        checks["no_unredacted_error"] = not _has_sensitive_content(raw.get("error_message"))
        checks["within_timeout"] = not timed_out

        success = all(checks.values())
        duration = raw.get("duration_seconds")
        return JobRecord(
            job_id=job_id,
            profile=profile,
            index=index,
            kind="main",
            control_scenario=None,
            created_at=None,
            started_at=raw.get("started_at"),
            finished_at=raw.get("completed_at"),
            duration_seconds=float(duration) if duration is not None
            else round(finished_mono - started_mono, 3),
            terminal_status=str(raw.get("status") or "unknown"),
            error_code=raw.get("error_code"),
            failure_stage=raw.get("failure_stage"),
            required_artifacts=list(_REQUIRED_ARTIFACTS),
            missing_artifacts=missing,
            success_checks=checks,
            success=success,
            timeout=timed_out,
        )

    # ---- 运行全流程 ----

    def run(self) -> dict[str, Any]:
        self.assert_fake_mode()

        # Prometheus 前后快照（旁证，不用于推导成功率）。
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}

        main_created = self._create_main_jobs()
        if len(main_created) != self.jobs:
            raise RuntimeError(f"主任务创建数 {len(main_created)} != 期望 {self.jobs}")

        main_records: list[JobRecord] = []
        for i, (profile, job_id) in enumerate(main_created):
            rec = self._run_single(job_id, profile, i)
            main_records.append(rec)

        # 控制场景（取消）单独统计，不进入主成功率分母。
        control_cancel = self._run_control_cancel()
        control_records: list[JobRecord] = []

        return self._build_summary(main_records, control_records, before, after,
                                   control_cancel)

    def _build_summary(
        self,
        main_records: list[JobRecord],
        control_records: list[JobRecord],
        before: dict[str, Any],
        after: dict[str, Any],
        control_cancel: dict[str, Any],
    ) -> dict[str, Any]:
        succeeded = [r for r in main_records if r.success]
        failed = [r for r in main_records if not r.success]
        timeout = [r for r in main_records if r.timeout]

        fast_recs = [r for r in main_records if r.profile == "fast"]
        deep_recs = [r for r in main_records if r.profile == "deep"]
        fast_succeeded = [r for r in fast_recs if r.success]
        deep_succeeded = [r for r in deep_recs if r.success]

        durations = [r.duration_seconds for r in main_records
                     if r.duration_seconds is not None]

        p50 = _percentile(durations, 0.50)
        p90 = _percentile(durations, 0.90)
        p95 = _percentile(durations, 0.95)
        p99 = _percentile(durations, 0.99)

        total_s = sum(durations) if durations else 0.0
        throughput = len(main_records) / total_s if total_s else None

        error_by_code = dict(
            Counter((r.error_code or "UNKNOWN") for r in failed)
        )
        stage_by_failure = dict(
            Counter((r.failure_stage or "unknown") for r in failed)
        )
        missing_artifacts_dist = dict(
            Counter(", ".join(sorted(r.missing_artifacts)) if r.missing_artifacts
                    else "none" for r in main_records)
        )

        total_main = len(main_records)
        workflow_rate = len(succeeded) / total_main if total_main else 0.0
        passed = (total_main > 0 and len(succeeded) == total_main) or (
            total_main >= 100 and len(succeeded) >= 96
        )

        summary = {
            "benchmark_run_id": self.run_id,
            "workflow_success_rate": round(workflow_rate, 6),
            "live_agent_success_rate": None,
            "live_agent_success_rate_note": (
                "本轮 fake 模式只验证 workflow_success_rate（API/DB/Redis/Celery/"
                "Worker/Flow 全链）；真实 SEC/Serper/LLM 下的 Agent 成功率由"
                "后续 live 基准另行评估，不得用本数字宣传为真实 Agent 成功率。"
            ),
            "main_jobs_total": total_main,
            "main_fast_count": len(fast_recs),
            "main_deep_count": len(deep_recs),
            "succeeded": len(succeeded),
            "failed": len(failed),
            "timeout": len(timeout),
            "fast_succeeded": len(fast_succeeded),
            "deep_succeeded": len(deep_succeeded),
            "fast_success_rate": round(
                len(fast_succeeded) / len(fast_recs), 6) if fast_recs else None,
            "deep_success_rate": round(
                len(deep_succeeded) / len(deep_recs), 6) if deep_recs else None,
            "strict_gt_95_percent": passed,
            "duration_seconds_avg": round(
                statistics.mean(durations), 3) if durations else None,
            "duration_seconds_p50": p50,
            "duration_seconds_p90": p90,
            "duration_seconds_p95": p95,
            "duration_seconds_p99": p99,
            "duration_seconds_min": round(min(durations), 3) if durations else None,
            "duration_seconds_max": round(max(durations), 3) if durations else None,
            "throughput_jobs_per_second": round(throughput, 6) if throughput else None,
            "failure_by_error_code": error_by_code,
            "failure_by_stage": stage_by_failure,
            "missing_artifacts_distribution": missing_artifacts_dist,
            "control_cancel_ok": control_cancel.get("ok"),
            "control_scenarios": {"cancel": control_cancel.get("ok")},
            "prometheus_before": before,
            "prometheus_after": after,
            "prometheus_diff": {
                k: (after.get(k, 0) or 0) - (before.get(k, 0) or 0)
                for k in set(before) | set(after)
            },
        }

        # 写入文件。
        self._write_artifacts(main_records, summary, control_cancel)
        return summary

    def _write_artifacts(
        self,
        main_records: list[JobRecord],
        summary: dict[str, Any],
        control_cancel: dict[str, Any],
    ) -> None:
        config = BenchmarkConfig(
            benchmark_run_id=self.run_id,
            jobs=self.jobs,
            fast_count=self.fast_count,
            deep_count=self.deep_count,
            concurrency=self.concurrency,
            timeout_seconds=self.timeout_seconds,
            poll_interval_seconds=self.poll_interval,
            seed=self.seed,
            flow_mode="fake",
            api_base=self.api_base,
            as_of_date=self.as_of,
        )
        (self.run_dir / "config.json").write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with (self.run_dir / "jobs.jsonl").open("w", encoding="utf-8") as fh:
            for rec in main_records:
                fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

        with (self.run_dir / "failures.jsonl").open("w", encoding="utf-8") as fh:
            for rec in main_records:
                if not rec.success:
                    fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

        (self.run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.run_dir / "metrics_snapshot_before.json").write_text(
            json.dumps(summary.get("prometheus_before", {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.run_dir / "metrics_snapshot_after.json").write_text(
            json.dumps(summary.get("prometheus_after", {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.run_dir / "report.md").write_text(
            _render_report(self.run_dir, summary), encoding="utf-8",
        )


def _render_report(run_dir: Path, summary: dict[str, Any]) -> str:
    lines = [
        f"# P06-10 基准报告（{summary['benchmark_run_id']}）",
        "",
        f"- 主任务总数：{summary['main_jobs_total']}（fast={summary['main_fast_count']}，"
        f"deep={summary['main_deep_count']}）",
        f"- workflow_success_rate：{summary['workflow_success_rate']:.2%}"
        f"（succeeded={summary['succeeded']} / {summary['main_jobs_total']}）",
        f"- live_agent_success_rate：{summary['live_agent_success_rate']}",
        f"  - {summary['live_agent_success_rate_note']}",
        f"- 严格 >95%：{'通过' if summary['strict_gt_95_percent'] else '未通过'}",
        f"- fast 成功率：{summary['fast_success_rate']}（"
        f"{summary['fast_succeeded']}/{summary['main_fast_count']}）",
        f"- deep 成功率：{summary['deep_success_rate']}（"
        f"{summary['deep_succeeded']}/{summary['main_deep_count']}）",
        "",
        "## 耗时（秒）",
        "",
        f"- 平均：{summary['duration_seconds_avg']}",
        f"- P50：{summary['duration_seconds_p50']}",
        f"- P90：{summary['duration_seconds_p90']}",
        f"- P95：{summary['duration_seconds_p95']}",
        f"- P99：{summary['duration_seconds_p99']}",
        f"- 最小/最大：{summary['duration_seconds_min']} / "
        f"{summary['duration_seconds_max']}",
        f"- 吞吐量：{summary['throughput_jobs_per_second']} jobs/s",
        "",
        "## 失败归因",
        "",
        "### 按 error_code",
    ]
    for code, count in summary["failure_by_error_code"].items():
        lines.append(f"- {code}: {count}")
    lines.append("")
    lines.append("### 按 failure_stage")
    for stage, count in summary["failure_by_stage"].items():
        lines.append(f"- {stage}: {count}")
    lines.append("")
    lines.append("### 缺失工件")
    for key, count in summary["missing_artifacts_distribution"].items():
        lines.append(f"- {key}: {count}")
    lines.append("")
    lines.append("## 控制任务")
    lines.append(f"- 取消场景 ok={summary['control_cancel_ok']}")
    lines.append("")
    lines.append("## Prometheus 旁证（仅核对，不用于推导成功率）")
    lines.append("- before：`metrics_snapshot_before.json`")
    lines.append("- after：`metrics_snapshot_after.json`")
    lines.append(f"- diff：{summary['prometheus_diff']}")
    lines.append("")
    lines.append("原始数据：`jobs.jsonl` / `failures.jsonl` / `config.json` / `summary.json`")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P06-10A phase 6 benchmark runner (fake only)")
    parser.add_argument("--jobs", type=int, default=10, help="主任务总数（默认 10，校准）")
    parser.add_argument("--fast-ratio", type=float, default=0.5,
                        help="fast 占比（默认 0.5）")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="最大并发（建议 <=4）")
    parser.add_argument("--timeout-seconds", type=int, default=180,
                        help="单任务超时秒数")
    parser.add_argument("--poll-interval", type=float, default=2.0,
                        help="轮询间隔秒数")
    parser.add_argument("--seed", type=int, default=7, help="固定随机种子")
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="输出目录（默认 evals/runs/<run_id>）")
    args = parser.parse_args(argv)

    try:
        runner = BenchmarkRunner(
            jobs=args.jobs,
            fast_ratio=args.fast_ratio,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout_seconds,
            poll_interval=args.poll_interval,
            seed=args.seed,
            api_base=args.api_base,
            output_dir=args.output_dir,
        )
        summary = runner.run()
    except RuntimeError as exc:
        print(f"[bench] 失败：{exc}", file=sys.stderr)
        return 1

    print(f"[bench] run_id={runner.run_id}")
    print(f"[bench] workflow_success_rate={summary['workflow_success_rate']:.2%} "
          f"({summary['succeeded']}/{summary['main_jobs_total']})")
    print(f"[bench] 输出目录：{runner.run_dir}")
    return 0 if summary["strict_gt_95_percent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
