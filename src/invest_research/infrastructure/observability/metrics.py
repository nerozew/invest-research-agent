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
    # P06-09C
    "http_requests_total",
    "http_request_duration_seconds",
    "http_requests_in_progress",
    "research_job_duration_seconds",
    "research_jobs_in_progress",
    "stale_recovery_total",
    "failure_total",
    "agent_runs_total",
    "agent_duration_seconds",
    "pack_validation_total",
    "schema_repair_total",
    "analysis_completeness_total",
    "tool_duration_seconds",
    "tool_cache_total",
    "llm_requests_total",
    "llm_request_duration_seconds",
    "llm_tokens_total",
    "llm_usage_missing_total",
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

# ---------------------------------------------------------------------------
# P06-09C 新增指标（label 全部白名单有限值）
# ---------------------------------------------------------------------------

# HTTP 请求耗时桶（秒）：毫秒级 API 到数秒级操作。
_HTTP_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    float("inf"),
)

# Job 总耗时桶（秒）：秒级 fake 到数十分钟 live。
_JOB_DURATION_BUCKETS = (
    1.0,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    1800.0,
    3600.0,
    float("inf"),
)

# Agent 耗时桶（秒）：模型调用毫秒级到分钟级。
_AGENT_DURATION_BUCKETS = (
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
    float("inf"),
)

# 工具耗时桶（秒）。
_TOOL_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    float("inf"),
)

# LLM 请求耗时桶（秒）：模型调用毫秒级到分钟级。
_LLM_DURATION_BUCKETS = (
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
    float("inf"),
)

# 一、HTTP RED
http_requests_total = Counter(
    "http_requests_total",
    "API 请求总数（method + 路由模板 + 状态分类 2xx/4xx/5xx）",
    ["method", "route", "status_class"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "API 请求真实持续秒数（按 method + 路由模板）",
    ["method", "route"],
    buckets=_HTTP_DURATION_BUCKETS,
)

http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "当前正在处理的 API 请求数（按 method + 路由模板）",
    ["method", "route"],
)

# 二、Job 维度
research_job_duration_seconds = Histogram(
    "research_job_duration_seconds",
    "研究任务真实总持续秒数（按档位与终态）",
    ["profile", "status"],
    buckets=_JOB_DURATION_BUCKETS,
)

research_jobs_in_progress = Gauge(
    "research_jobs_in_progress",
    "当前正在执行的研究任务数（按档位）",
    ["profile"],
)

stale_recovery_total = Counter(
    "stale_recovery_total",
    "Worker 启动收口 stale running Job 的结果（recovered / none）",
    ["result"],
)

failure_total = Counter(
    "failure_total",
    "任务失败总数（按阶段与稳定错误码）",
    ["stage", "error_code"],
)

# 三、Agent 维度
agent_runs_total = Counter(
    "agent_runs_total",
    "Agent 执行总数（role + 档位 + 脱敏 provider/model + 状态）",
    ["role", "profile", "provider", "model", "status"],
)

agent_duration_seconds = Histogram(
    "agent_duration_seconds",
    "Agent 执行真实持续秒数（role + 档位 + 脱敏 provider/model + 状态）",
    ["role", "profile", "provider", "model", "status"],
    buckets=_AGENT_DURATION_BUCKETS,
)

# 四、PackBoundary 维度
pack_validation_total = Counter(
    "pack_validation_total",
    "PackBoundary 校验结果（阶段 + pack 类型 + 结果 + 稳定错误码）",
    ["stage", "pack_type", "result", "error_code"],
)

schema_repair_total = Counter(
    "schema_repair_total",
    "PackBoundary 有限修复结果（阶段 + pack 类型 + 结果）",
    ["stage", "pack_type", "result"],
)

analysis_completeness_total = Counter(
    "analysis_completeness_total",
    "Analysis pack 完整性结果分布（complete/partial/unavailable）",
    ["status"],
)

# 五、工具与缓存
tool_duration_seconds = Histogram(
    "tool_duration_seconds",
    "工具执行真实持续秒数（按工具与状态）",
    ["tool", "status"],
    buckets=_TOOL_DURATION_BUCKETS,
)

tool_cache_total = Counter(
    "tool_cache_total",
    "工具缓存结果（hit/miss/stored/skipped）",
    ["tool", "result"],
)

# 六、LLM
llm_requests_total = Counter(
    "llm_requests_total",
    "LLM 调用总数（脱敏 provider/model + 角色 + 状态）",
    ["provider", "model", "role", "status"],
)

llm_request_duration_seconds = Histogram(
    "llm_request_duration_seconds",
    "LLM 调用真实持续秒数（脱敏 provider/model + 角色 + 状态）",
    ["provider", "model", "role", "status"],
    buckets=_LLM_DURATION_BUCKETS,
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "LLM Token 用量（必须来自真实模型响应 usage；type 只允许 input/output/cached_input）",
    ["provider", "model", "role", "type"],
)

llm_usage_missing_total = Counter(
    "llm_usage_missing_total",
    "LLM 响应不包含真实 usage 的次数（不得伪造为 0）",
    ["provider", "model", "role"],
)
