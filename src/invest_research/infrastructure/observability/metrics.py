"""Prometheus 指标（P05-06 + P06-06C 业务指标接线）。

依据 docs/04 §8.2：research_jobs_total、workflow_steps_total、
workflow_step_duration_seconds、tool_calls_total、tool_retries_total、
quality_gate_failures_total、stale_running_steps 等。

P06-06C 事件语义（见 docs/04 §8.2 + 本任务计划）：
- research_jobs_total：Job 真实进入关键状态时计数，label 只允许 status；
- workflow_steps_total：Step 进入终态时计数，label 只允许 step/status；
- workflow_step_duration_seconds：Histogram，Step 完成/失败时 observe 真实持续秒数，
  label 只允许 step；
- tool_calls_total：工具调用完成时按 tool/status 计数；
- tool_retries_total：真实可重试失败时按 tool/error_code 计数；
- quality_gate_failures_total：质量门禁失败时按稳定 gate 类别计数；
- stale_running_steps：Gauge，当前 stale 步骤数量。

约束：不添加 job_id/URL/公司名高基数 label；不记录密钥、Prompt、Authorization。
"""

from __future__ import annotations

import os
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram

# P06-09 fix（live 直接运行/测试路径）：prometheus_client 多进程模式要求
# PROMETHEUS_MULTIPROC_DIR 目录在首次写 mmap .db 文件前已存在。
# Worker 启动时 `worker.py` 会设置该环境变量；此处无论谁先 import，
# 都保证目录存在（exist_ok=True 幂等，不影响 worker 路径）。
_multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
if _multiproc_dir:
    Path(_multiproc_dir).mkdir(parents=True, exist_ok=True)

__all__ = [
    "research_jobs_total",
    "workflow_steps_total",
    "workflow_step_duration_seconds",
    "tool_calls_total",
    "tool_retries_total",
    "quality_gate_failures_total",
    "stale_running_steps",
]

# 步骤耗时 Histogram 桶（秒）：覆盖本地 fake 运行（秒级）到 live 长任务（分钟级）。
_STEP_DURATION_BUCKETS = (
    0.1,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    1800.0,
    float("inf"),
)

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

workflow_step_duration_seconds = Histogram(
    "workflow_step_duration_seconds",
    "工作流步骤真实持续秒数（按步骤）",
    ["step"],
    buckets=_STEP_DURATION_BUCKETS,
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
