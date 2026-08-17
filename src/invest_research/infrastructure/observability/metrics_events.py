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
    agent_runs_total,
    analysis_completeness_total,
    failure_total,
    http_request_duration_seconds,
    http_requests_in_progress,
    http_requests_total,
    llm_request_duration_seconds,
    llm_requests_total,
    llm_tokens_total,
    llm_usage_missing_total,
    pack_validation_total,
    quality_gate_failures_total,
    research_job_duration_seconds,
    research_jobs_in_progress,
    research_jobs_total,
    schema_repair_total,
    stale_recovery_total,
    stale_running_steps,
    tool_cache_total,
    tool_calls_total,
    tool_duration_seconds,
    tool_retries_total,
    workflow_step_duration_seconds,
    workflow_steps_total,
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
    "count_pack_validation",
    "count_schema_repair",
    "count_analysis_completeness",
    "observe_tool_duration",
    "count_tool_cache",
    "count_llm_request",
    "observe_llm_duration",
    "count_llm_tokens",
    "count_llm_usage_missing",
    "label_provider_model",
]

_LOGGER = logging.getLogger(__name__)


def _safe_inc(
    counter: Any,
    *,
    label_values: tuple[str, ...] | None = None,
) -> None:
    """安全计数：任何异常只记录脱敏日志（不改变业务结果）。"""
    try:
        if label_values is None:
            counter.inc()
        else:
            counter.labels(*label_values).inc()
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
_PACK_RESULTS: frozenset[str] = frozenset(
    {"success", "failed", "repaired", "rejected", "skipped"}
)
# LLM token type 白名单。
_LLM_TOKEN_TYPES: frozenset[str] = frozenset({"input", "output", "cached_input"})


def label_provider_model(base_url: str, model: str) -> tuple[str, str]:
    """把 base_url + model 映射为脱敏 provider/model label（不暴露完整 URL）。

    - base_url 只用于识别供应商（dashscope→qwen、deepseek→deepseek、其它→openai_compatible）；
    - model 直接使用配置模型名（稳定小基数，如 qwen-max / deepseek-chat）；
    - 绝不把 base_url / api_key 放进任何 label。
    """
    lowered = (base_url or "").lower()
    if "dashscope" in lowered:
        provider = "qwen"
    elif "deepseek" in lowered:
        provider = "deepseek"
    elif "openai" in lowered:
        provider = "openai"
    else:
        provider = "openai_compatible"
    return provider, (model or "unknown").strip().lower() or "unknown"


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
    _safe_inc(
        http_requests_total, label_values=(method, route, _status_class(status_code))
    )


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


def count_agent_run(
    role: str, profile: str, provider: str, model: str, status: str
) -> None:
    """Agent 执行完成（role 白名单过滤；provider/model 为脱敏标签）。"""
    if role not in _AGENT_ROLES:
        return
    if status not in ("success", "failure"):
        return
    _safe_inc(
        agent_runs_total, label_values=(role, profile, provider, model, status)
    )


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


# ---- 四、PackBoundary 维度 ----


def count_pack_validation(
    stage: str, pack_type: str, result: str, error_code: str
) -> None:
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
        llm_tokens_total.labels(
            provider=provider, model=model, role=role, type=token_type
        ).inc(amount)
    except Exception:  # noqa: BLE001 - 监控写入尽力而为
        _LOGGER.warning("metrics_inc_failed metric=llm_tokens_total")


def count_llm_usage_missing(provider: str, model: str, role: str) -> None:
    """LLM 响应不包含真实 usage 的次数（不得伪造 Token 为 0）。"""
    _safe_inc(llm_usage_missing_total, label_values=(provider, model, role))
