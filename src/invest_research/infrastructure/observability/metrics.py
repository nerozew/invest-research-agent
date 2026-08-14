"""Prometheus 指标（P05-06）。

依据 docs/04 §8.2：research_jobs_total、workflow_steps_total、tool_calls_total、
tool_retries_total、quality_gate_failures_total、stale_running_steps 等。
不添加 job_id/URL/公司名高基数 label。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

__all__ = [
    "research_jobs_total",
    "workflow_steps_total",
    "tool_calls_total",
    "tool_retries_total",
    "quality_gate_failures_total",
    "stale_running_steps",
]

research_jobs_total = Counter(
    "research_jobs_total",
    "创建的研究任务总数（按状态）",
    ["status"],
)

workflow_steps_total = Counter(
    "workflow_steps_total",
    "工作流步骤执行总数（按步骤与状态）",
    ["step", "status"],
)

tool_calls_total = Counter(
    "tool_calls_total",
    "工具调用总数（按工具与状态）",
    ["tool", "status"],
)

tool_retries_total = Counter(
    "tool_retries_total",
    "工具重试总数（按工具与错误码）",
    ["tool", "error_code"],
)

quality_gate_failures_total = Counter(
    "quality_gate_failures_total",
    "质量门禁失败总数（按门禁）",
    ["gate"],
)

stale_running_steps = Gauge(
    "stale_running_steps",
    "当前被标记为 stale 的 running 步骤数",
)
