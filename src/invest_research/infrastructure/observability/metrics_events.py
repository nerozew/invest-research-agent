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
    quality_gate_failures_total,
    research_jobs_total,
    stale_running_steps,
    tool_calls_total,
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
