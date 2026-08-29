"""P06-06C：业务指标事件辅助（脱敏写入，不改变业务结果）。

提供统一的指标写入辅助函数，供 API/Worker/Flow/工具/质量门禁在各真实事件点调用。

约束（对齐任务要求）：
- 指标写入绝对不改变业务成功/失败结果：内部 try/except，失败只记录脱敏日志；
- 监控异常只能记录脱敏日志（不含异常全文中的 key/prompt/Authorization）；
- at-least-once 与重复 Celery 投递不得明显重复计数：
  只在确定性状态转换成功（rowcount>0）或真实终态发生时计数；
- 不允许高基数 label；不记录 URL、job_id、公司名、异常全文或 API Key。
"""

from __future__ import annotations

import logging
from typing import Any

from invest_research.infrastructure.observability.metrics import (
    agent_duration_seconds,
    agent_iteration_limit_total,
    agent_iteration_total,
    agent_max_iteration_total,
    agent_runs_total,
    analysis_completeness_total,
    crewai_tool_calls_total,
    failure_total,
    http_request_duration_seconds,
    http_requests_in_progress,
    http_requests_total,
    job_cost_usd_total,
    job_tokens_total,
    job_tool_calls_total,
    llm_request_duration_seconds,
    llm_requests_total,
    llm_response_kind_total,
    llm_tokens_total,
    llm_usage_missing_total,
    pack_validation_total,
    quality_gate_failures_total,
    report_invalid_total,
    research_job_duration_seconds,
    research_jobs_in_progress,
    research_jobs_total,
    research_prefetch_total,
    revision_total,
    schema_repair_total,
    stage_duration_seconds,
    stale_recovery_total,
    stale_running_steps,
    tool_budget_exhausted_total,
    tool_cache_total,
    tool_calls_total,
    tool_duration_seconds,
    tool_retries_total,
    workflow_step_duration_seconds,
    workflow_steps_total,
    writer_direct_duration_seconds,
    writer_direct_output_chars,
    writer_direct_requests_total,
    writer_direct_retry_total,
    writer_output_chars,
    writer_recovery_total,
    writer_response_capture_total,
    writer_response_length_chars,
)

__all__ = [
    "count_research_job",
    "count_step_terminal",
    "count_tool_call",
    "count_tool_retry",
    "count_quality_gate_failure",
    "set_stale_running_steps",
    # P06-09C
    "observe_http_request",
    "count_http_requests",
    "set_http_in_progress",
    "observe_research_job",
    "set_research_job_in_progress",
    "count_stale_recovery",
    "count_failure",
    "count_agent_run",
    "observe_agent_duration",
    "count_agent_iteration_limit",
    "count_pack_validation",
    "count_schema_repair",
    "count_analysis_completeness",
    "observe_tool_duration",
    "count_tool_cache",
    "count_tool_budget_exhausted",
    "count_research_prefetch",
    "count_llm_request",
    "observe_llm_duration",
    "count_llm_tokens",
    "count_llm_usage_missing",
    "label_provider_model",
    # P06-11H
    "count_writer_response_capture",
    "count_writer_recovery",
    "observe_writer_response_length",
    # P06-11I
    "count_writer_direct_request",
    "observe_writer_direct_duration",
    "observe_writer_direct_output_chars",
    "count_writer_direct_retry",
    "observe_stage_duration",
    "count_report_invalid",
    "observe_writer_output_chars",
    "count_agent_max_iteration",
    "count_agent_iteration",
    "count_crewai_tool_call",
    "count_llm_response_kind",
]

_LOGGER = logging.getLogger(__name__)


def _safe_inc(
    counter: Any,
    *,
    label_values: tuple[str, ...] | None = None,
    value: int | float | None = None,
) -> None:
    """安全计数：任何异常只记录脱敏日志（不改变业务结果）。"""
    try:
        if label_values is None:
            if value is None:
                counter.inc()
            else:
                counter.inc(value)
        else:
            labeled = counter.labels(*label_values)
            if value is None:
                labeled.inc()
            else:
                labeled.inc(value)
    except Exception:  # noqa: BLE001 - 监控写入尽力而为，绝不中断业务
        _LOGGER.warning("metrics_inc_failed metric=%s", getattr(counter, "_name", "unknown"))


def count_research_job(status: str) -> None:
    """Job 真实进入关键状态时计数（label 只允许 status）。"""
    _safe_inc(research_jobs_total, label_values=(status,))


def count_step_terminal(step: str, status: str, duration_seconds: float | None) -> None:
    """Step 进入终态时计数并 observe 真实持续秒数（step/status label；Histogram 只 step）。

    - ``duration_seconds``：None（无法取得 started_at）时只计数不 observe。
    """
    _safe_inc(workflow_steps_total, label_values=(step, status))
    if duration_seconds is not None:
        _safe_obs(workflow_step_duration_seconds, (step,), duration_seconds)


def _safe_obs(
    histogram: Any,
    label_values: tuple[str, ...],
    value: float,
) -> None:
    """安全 observe：非法/异常值不写（不改变业务结果）。"""
    if value < 0:
        return
    try:
        histogram.labels(*label_values).observe(value)
    except Exception:  # noqa: BLE001 - 监控写入尽力而为
        _LOGGER.warning("metrics_observe_failed metric=%s", getattr(histogram, "_name", "unknown"))


def count_tool_call(tool: str, status: str) -> None:
    """工具调用完成时按 tool/status 计数（成功/失败）。"""
    _safe_inc(tool_calls_total, label_values=(tool, status))


def count_tool_retry(tool: str, error_code: str) -> None:
    """真实重试（可重试失败）时按 tool/error_code 计数。

    P06-06C 说明：当前生产代码的工具失败会返回 ToolFailure（错误分类），
    tenacity 重试循环尚未接入工具层。此处对「已分类为可重试且失败」的事件计数，
    既保留重试语义又不重复计数；未来接入真实重试循环后应在 before_sleep
    回调处调用，避免与失败事件重复计数。
    """
    _safe_inc(tool_retries_total, label_values=(tool, error_code))


def count_quality_gate_failure(gate: str) -> None:
    """质量门禁失败时按稳定 gate 类别计数（issue.code 是稳定类别，非自由文本）。"""
    _safe_inc(quality_gate_failures_total, label_values=(gate,))


def set_stale_running_steps(value: int) -> None:
    """设置 stale_running_steps Gauge（Worker 启动扫描后按真实恢复数更新）。"""
    try:
        stale_running_steps.set(value)
    except Exception:  # noqa: BLE001 - 监控写入尽力而为
        _LOGGER.warning("metrics_set_failed metric=stale_running_steps")


# ---------------------------------------------------------------------------
# P06-09C：脱敏写入辅助（所有 label 白名单有限值；绝不记录 URL/job_id/公司名）
# ---------------------------------------------------------------------------

# Agent role 白名单（任务要求只允许 research/analysis/writer/revision）。
_AGENT_ROLES: frozenset[str] = frozenset({"research", "analysis", "writer", "revision"})
# pack 校验结果白名单（成功/失败/修复成功/拒绝/跳过）。
_PACK_RESULTS: frozenset[str] = frozenset({"success", "failed", "repaired", "rejected", "skipped"})
# LLM token type 白名单。
_LLM_TOKEN_TYPES: frozenset[str] = frozenset({"input", "output", "cached_input"})


def label_provider_model(
    vendor: str | None = None,
    model: str = "",
    base_url: str | None = None,
) -> tuple[str, str]:
    """把 vendor/base_url + model 映射为脱敏 provider/model label（不暴露完整 URL）。

    P06-11：优先使用显式 vendor（qwen/deepseek/generic），不再依赖 base_url 猜测；
    仅当 vendor 缺失或为 generic 时才回退 base_url 识别（dashscope→qwen、
    deepseek→deepseek、openai→openai、其它→openai_compatible）。
    - model 直接使用配置模型名（稳定小基数，如 qwen-max / deepseek-v4-flash）；
    - 绝不把 base_url / api_key 放进任何 label。
    """
    vendor_lbl = (vendor or "").strip().lower()
    if vendor_lbl in ("qwen", "deepseek", "openai", "generic", "openai_compatible"):
        provider = vendor_lbl
    else:
        lowered = (base_url or "").lower()
        if "dashscope" in lowered:
            provider = "qwen"
        elif "deepseek" in lowered:
            provider = "deepseek"
        elif "openai" in lowered:
            provider = "openai"
        else:
            provider = "openai_compatible"
    return provider, (model or "").strip().lower() or "unknown"


# ---- 一、HTTP RED ----


def _status_class(status_code: int) -> str:
    """把 HTTP 状态码归类为 2xx/4xx/5xx（避免每个状态码产生过多序列）。"""
    code = int(status_code)
    if code >= 500:
        return "5xx"
    if code >= 400:
        return "4xx"
    return "2xx"


def count_http_requests(method: str, route: str, status_code: int) -> None:
    """记录一次 HTTP 请求（method + 路由模板 + 状态分类）。"""
    _safe_inc(http_requests_total, label_values=(method, route, _status_class(status_code)))


def observe_http_request(method: str, route: str, duration_seconds: float) -> None:
    """记录 HTTP 请求耗时（秒，非负才写入）。"""
    _safe_obs(http_request_duration_seconds, (method, route), duration_seconds)


def set_http_in_progress(method: str, route: str, delta: int) -> None:
    """HTTP 请求进行中 Gauge 增减（进入 +1，离开 -1）。"""
    if delta not in (-1, 1):
        return
    try:
        gauge = http_requests_in_progress.labels(method=method, route=route)
        if delta > 0:
            gauge.inc()
        else:
            gauge.dec()
    except Exception:  # noqa: BLE001 - 监控写入尽力而为
        _LOGGER.warning("metrics_gauge_delta_failed metric=http_requests_in_progress")


# ---- 二、Job 维度 ----


def observe_research_job(profile: str, status: str, duration_seconds: float) -> None:
    """Job 到达终态时记录真实总持续秒数（profile + status）。"""
    _safe_obs(research_job_duration_seconds, (profile, status), duration_seconds)


def set_research_job_in_progress(profile: str, delta: int) -> None:
    """Job 执行中 Gauge 增减（进入 +1，离开 -1）。"""
    if delta not in (-1, 1):
        return
    try:
        gauge = research_jobs_in_progress.labels(profile=profile)
        if delta > 0:
            gauge.inc()
        else:
            gauge.dec()
    except Exception:  # noqa: BLE001 - 监控写入尽力而为
        _LOGGER.warning("metrics_gauge_delta_failed metric=research_jobs_in_progress")


def count_stale_recovery(result: str) -> None:
    """Worker 启动收口 stale running Job 的结果（recovered / none）。"""
    if result not in ("recovered", "none"):
        return
    _safe_inc(stale_recovery_total, label_values=(result,))


def count_failure(stage: str, error_code: str) -> None:
    """任务失败（按阶段 + 稳定错误码；error_code 来自 failure_classifier 白名单）。"""
    _safe_inc(failure_total, label_values=(stage, error_code))


# ---- 三、Agent 维度 ----


def count_agent_run(role: str, profile: str, provider: str, model: str, status: str) -> None:
    """Agent 执行完成（role 白名单过滤；provider/model 为脱敏标签）。"""
    if role not in _AGENT_ROLES:
        return
    if status not in ("success", "failure"):
        return
    _safe_inc(agent_runs_total, label_values=(role, profile, provider, model, status))


def observe_agent_duration(
    role: str,
    profile: str,
    provider: str,
    model: str,
    status: str,
    duration_seconds: float,
) -> None:
    """Agent 执行真实持续秒数（role 白名单过滤）。"""
    if role not in _AGENT_ROLES:
        return
    if status not in ("success", "failure"):
        return
    _safe_obs(
        agent_duration_seconds,
        (role, profile, provider, model, status),
        duration_seconds,
    )


def count_agent_iteration_limit(role: str, profile: str) -> None:
    """记录一次 Agent 迭代预算耗尽（只接受稳定角色与 fast/deep）。"""
    if role not in _AGENT_ROLES or profile not in ("fast", "deep"):
        return
    _safe_inc(agent_iteration_limit_total, label_values=(role, profile))


def count_revision_attempted() -> None:
    """记录一次有界 Writer 修订尝试（P06-11F，result=attempted）。"""
    _safe_inc(revision_total, label_values=("attempted",))


def count_revision_succeeded(succeeded: bool) -> None:
    """记录有界 Writer 修订结果（result=succeeded；失败时仅 attempted 已计数）。"""
    if succeeded:
        _safe_inc(revision_total, label_values=("succeeded",))


# ---- 四、PackBoundary 维度 ----


def count_pack_validation(stage: str, pack_type: str, result: str, error_code: str) -> None:
    """PackBoundary 校验结果（result 白名单过滤；error_code 稳定短码）。"""
    if result not in _PACK_RESULTS:
        return
    _safe_inc(
        pack_validation_total,
        label_values=(stage, pack_type, result, error_code),
    )


def count_schema_repair(stage: str, pack_type: str, result: str) -> None:
    """PackBoundary 有限修复结果（result 白名单过滤）。"""
    if result not in _PACK_RESULTS:
        return
    _safe_inc(schema_repair_total, label_values=(stage, pack_type, result))


def count_analysis_completeness(status: str) -> None:
    """Analysis pack 完整性结果（complete/partial/unavailable）。"""
    if status not in ("complete", "partial", "unavailable"):
        return
    _safe_inc(analysis_completeness_total, label_values=(status,))


# ---- 五、工具与缓存 ----


def observe_tool_duration(tool: str, status: str, duration_seconds: float) -> None:
    """工具执行真实持续秒数（按工具与成功/失败状态）。"""
    if status not in ("success", "failure"):
        return
    _safe_obs(tool_duration_seconds, (tool, status), duration_seconds)


def count_tool_cache(tool: str, result: str) -> None:
    """工具缓存结果（hit/miss/stored/skipped）。"""
    if result not in ("hit", "miss", "stored", "skipped"):
        return
    _safe_inc(tool_cache_total, label_values=(tool, result))


def count_tool_budget_exhausted(tool: str) -> None:
    """工具调用本地硬预算耗尽（P06-11G，按稳定工具名，不含 job_id/company/cik）。"""
    _safe_inc(tool_budget_exhausted_total, label_values=(tool,))


def count_research_prefetch(status: str) -> None:
    """研究预取执行结果（P06-11G：ok/partial/failed/skipped 白名单过滤）。"""
    if status not in ("ok", "partial", "failed", "skipped"):
        return
    _safe_inc(research_prefetch_total, label_values=(status,))


# ---- 六、LLM ----


def count_llm_request(provider: str, model: str, role: str, status: str) -> None:
    """LLM 调用（脱敏 provider/model + role + 成功/失败状态）。"""
    if status not in ("success", "failure"):
        return
    _safe_inc(llm_requests_total, label_values=(provider, model, role, status))


def observe_llm_duration(
    provider: str, model: str, role: str, status: str, duration_seconds: float
) -> None:
    """LLM 调用真实持续秒数。"""
    if status not in ("success", "failure"):
        return
    _safe_obs(
        llm_request_duration_seconds,
        (provider, model, role, status),
        duration_seconds,
    )


def count_llm_tokens(provider: str, model: str, role: str, token_type: str, amount: int) -> None:
    """记录真实模型响应 usage 中的 Token（type 白名单；amount 非负才写）。

    必须由调用方从真实模型响应 usage 提取；无 usage 时调用 ``count_llm_usage_missing``，
    绝不能把 0 或字符串长度估算当真实 Token。
    """
    if token_type not in _LLM_TOKEN_TYPES:
        return
    if amount is None or amount < 0:
        return
    try:
        llm_tokens_total.labels(provider=provider, model=model, role=role, type=token_type).inc(
            amount
        )
    except Exception:  # noqa: BLE001 - 监控写入尽力而为
        _LOGGER.warning("metrics_inc_failed metric=llm_tokens_total")


def count_llm_usage_missing(provider: str, model: str, role: str) -> None:
    """LLM 响应不包含真实 usage 的次数（不得伪造 Token 为 0）。"""
    _safe_inc(llm_usage_missing_total, label_values=(provider, model, role))


# ---- 七、P06-11H：Writer 响应捕获与有限恢复 ----


def count_writer_response_capture(result: str) -> None:
    """Writer 每轮响应捕获结果（result=accepted/duplicate/too_long/empty）。"""
    if result not in ("accepted", "duplicate", "too_long", "empty"):
        return
    _safe_inc(writer_response_capture_total, label_values=(result,))


def count_writer_recovery(result: str) -> None:
    """Writer 有限恢复结果（result=recovered/rejected/none）。"""
    if result not in ("recovered", "rejected", "none"):
        return
    _safe_inc(writer_recovery_total, label_values=(result,))


def observe_writer_response_length(char_length: int) -> None:
    """记录 Writer 捕获响应正文长度（字符，只记录长度不记录正文）。"""
    if char_length is None or char_length < 0:
        return
    _safe_obs(writer_response_length_chars, ("writer",), float(char_length))


# ---- 八、P06-11I：Direct Writer（无工具单轮调用）----

# Direct Writer 调用结果白名单（status 只允许稳定低基数值）。
_WRITER_DIRECT_STATUSES: frozenset[str] = frozenset(
    {"success", "empty", "length", "invalid", "error"}
)
# Direct Writer 有限重试原因白名单（reason 只允许稳定低基数值）。
_WRITER_DIRECT_RETRY_REASONS: frozenset[str] = frozenset(
    {"empty", "length", "too_short", "missing_section"}
)


def count_writer_direct_request(status: str) -> None:
    """Direct Writer 无工具调用结果计数（status 白名单过滤）。"""
    if status not in _WRITER_DIRECT_STATUSES:
        return
    _safe_inc(writer_direct_requests_total, label_values=(status,))


def observe_writer_direct_duration(status: str, duration_s: float) -> None:
    """Direct Writer 单个无工具调用耗时（秒，非负且 status 白名单才写）。"""
    if status not in _WRITER_DIRECT_STATUSES:
        return
    _safe_obs(writer_direct_duration_seconds, (status,), duration_s)


def observe_writer_direct_output_chars(status: str, char_length: int) -> None:
    """Direct Writer 输出 Markdown 长度（字符，只记录长度不记录正文）。"""
    if status not in _WRITER_DIRECT_STATUSES:
        return
    if char_length is None or char_length < 0:
        return
    _safe_obs(writer_direct_output_chars, (status,), float(char_length))


def count_writer_direct_retry(reason: str) -> None:
    """Direct Writer 触发有限重试的原因计数（reason 白名单过滤）。"""
    if reason not in _WRITER_DIRECT_RETRY_REASONS:
        return
    _safe_inc(writer_direct_retry_total, label_values=(reason,))


# ---------------------------------------------------------------------------
# 八、P06-11J：完整调用链低基数指标辅助（尽力而为，label 白名单）
# ---------------------------------------------------------------------------


def count_llm_response_kind(role: str, kind: str) -> None:
    """LLM 响应类型分布（kind=content/tool_call/empty）。"""
    _safe_inc(llm_response_kind_total, label_values=(role, kind))


def count_crewai_tool_call(role: str, tool: str, status: str) -> None:
    """CrewAI 工具循环调用次数（role + 稳定工具名 + status）。"""
    _safe_inc(crewai_tool_calls_total, label_values=(role, tool, status))


def count_agent_iteration(role: str, result: str) -> None:
    """Agent 迭代次数（result=success/failure）。"""
    _safe_inc(agent_iteration_total, label_values=(role, result))


def count_agent_max_iteration(role: str) -> None:
    """Agent 达到 max_iter 次数（按角色）。"""
    _safe_inc(agent_max_iteration_total, label_values=(role,))


def observe_writer_output_chars(status: str, char_length: int) -> None:
    """Writer 最终输出长度（只记录长度，不记录正文）。"""
    _safe_obs(writer_output_chars, (status,), float(char_length))


def count_report_invalid(reason: str) -> None:
    """REPORT_INVALID 触发原因分类（低基数 reason 白名单）。"""
    _safe_inc(report_invalid_total, label_values=(reason,))


def observe_stage_duration(stage: str, status: str, duration_s: float) -> None:
    """真实阶段 Span 耗时（秒；stage/status，不记录 job_id/company）。"""
    _safe_obs(stage_duration_seconds, (stage, status), duration_s)


def record_job_performance(
    *,
    profile: str,
    mode: str,
    status: str,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    tool_calls: int,
    cost_usd: float | None,
) -> None:
    """WS3：已发布任务的 token/工具调用/成本聚合（低基数 label；缺失 token 不伪造 0）。

    - 只在真实终态记录一次（避免重复 Celery 投递重复计数）；
    - cost 由调用方按 pricing 估算，缺失传 None 不计数。
    """
    mode = mode or "legacy"
    status = status or "unknown"
    if total_tokens is not None and total_tokens >= 0:
        _safe_inc(job_tokens_total, label_values=(profile, mode, "total"), value=total_tokens)
    if input_tokens is not None and input_tokens >= 0:
        _safe_inc(job_tokens_total, label_values=(profile, mode, "input"), value=input_tokens)
    if output_tokens is not None and output_tokens >= 0:
        _safe_inc(job_tokens_total, label_values=(profile, mode, "output"), value=output_tokens)
    if tool_calls and tool_calls > 0:
        _safe_inc(job_tool_calls_total, label_values=(profile, mode), value=tool_calls)
    if cost_usd is not None and cost_usd >= 0:
        _safe_inc(job_cost_usd_total, label_values=(profile, mode, status), value=cost_usd)
