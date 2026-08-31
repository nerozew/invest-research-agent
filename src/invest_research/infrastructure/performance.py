"""P05.5 性能记录（PerformanceRecorder）。

职责：
- 记录 Research/Analysis/Writer 三个 Agent 的耗时；
- 记录 SEC/Serper/下载/解析等工具的耗时与调用次数；
- 能安全获取时记录 LLM token usage，否则写 None；
- 汇总为 performance dict，由 flow_wiring 写入 07_manifest.json。

安全边界：只记录耗时、调用次数与 token 计数；绝不记录密钥、
Authorization 头、完整 Prompt 或任何模型输入/输出正文。

依赖边界：仅标准库；禁止导入 CrewAI/httpx/任何供应商 SDK。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

# 可安全导出的 token usage 键（UsageMetrics 的数值字段，不含任何敏感内容）
_TOKEN_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_prompt_tokens",
    "successful_requests",
)


def extract_token_usage(usage: object | None) -> dict[str, int] | None:
    """从 CrewOutput.token_usage（UsageMetrics 或 dict）安全提取 token 计数。

    - usage 为 None 或 total_tokens 缺失/为 0 时返回 None（表示不可获取）；
    - 只提取数值计数字段，不触碰 prompt/输出正文，不触碰任何凭据。
    """
    if usage is None:
        return None
    total = getattr(usage, "total_tokens", None)
    if total is None and isinstance(usage, dict):
        total = usage.get("total_tokens")
    if total is None or int(total) <= 0:
        return None

    result: dict[str, int] = {}
    for key in _TOKEN_USAGE_KEYS:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        if value is None and isinstance(usage, dict):
            value = 0
        result[key] = int(value or 0)
    return result


class PerformanceRecorder:
    """线程安全的耗时/调用统计器（并行 pre-fetch 会跨线程写工具统计）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # role -> duration_ms（research/analysis/writer）
        self._agents: dict[str, int] = {}
        # tool_name -> {"calls": int, "total_ms": float}
        self._tools: dict[str, dict[str, float]] = {}
        self._cache_hits: dict[str, int] = {}
        self._token_usage: dict[str, int] | None = None

    def record_agent(self, role: str, duration_ms: int) -> None:
        """记录单个 Agent 的耗时（毫秒，非负整数）。"""
        with self._lock:
            self._agents[role] = max(0, int(duration_ms))

    @contextmanager
    def timed_tool(self, tool_name: str) -> Iterator[None]:
        """上下文管理器：统计一次工具调用的耗时与次数。"""
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            with self._lock:
                entry = self._tools.setdefault(tool_name, {"calls": 0.0, "total_ms": 0.0})
                entry["calls"] += 1.0
                entry["total_ms"] += elapsed_ms

    def record_cache_hit(self, tool_name: str) -> None:
        """记录一次缓存命中（不计入网络耗时，单列计数）。"""
        with self._lock:
            self._cache_hits[tool_name] = self._cache_hits.get(tool_name, 0) + 1

    _TOKEN_SUM_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_prompt_tokens")

    def set_token_usage(self, usage: dict[str, int] | None) -> None:
        """写入安全的 token usage 汇总（None 表示不可获取）。

        覆盖式写入（兼容 legacy 单次 kickoff 路径）。
        """
        with self._lock:
            self._token_usage = dict(usage) if usage is not None else None

    def add_token_usage(self, usage: dict[str, int] | None) -> None:
        """按 Job 累加一次真实 usage（多阶段路径：Research/Analysis/Writer/Finalizer）。

        - 只累加数值计数字段；usage 为 None 时不改动当前汇总；
        - 已有的非 None 汇总与新 usage 逐字段相加（不重复计数）；
        - 无任何 usage 时保持 None（绝不从字符数/日志估算）。
        """
        if usage is None:
            return
        with self._lock:
            if self._token_usage is None:
                self._token_usage = {}
            for key in self._TOKEN_SUM_KEYS:
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    self._token_usage[key] = int(self._token_usage.get(key, 0)) + int(value)

    def snapshot(self) -> dict[str, Any]:
        """汇总为 manifest.performance 结构（不包含任何敏感字段）。"""
        with self._lock:
            tools = {
                name: {
                    "calls": int(entry["calls"]),
                    "total_ms": round(entry["total_ms"], 3),
                }
                for name, entry in self._tools.items()
            }
            return {
                "agents": {role: {"duration_ms": ms} for role, ms in self._agents.items()},
                "tools": tools,
                "cache_hits": dict(self._cache_hits),
                "token_usage": self._token_usage,
            }
