"""P05.5-fix 每 Job 工具调用硬预算（ToolBudget）。

max_rpm 只限制每分钟模型请求速率，不是工具总调用上限。ToolBudget 是每 Job
独立的工具执行计数（不跨 Job 共享、无全局状态）；达到上限后调用方返回
BUDGET_EXHAUSTED 结构化失败，禁止无限重复调用。

依赖边界：仅标准库；禁止导入 CrewAI/httpx/供应商 SDK。
"""

from __future__ import annotations

import threading
from typing import Mapping

# 每 Job 工具执行硬上限（键与 real_tools 包装层的工具名一致，小写下划线；
# 未列出的工具（如本地 document_parser）默认无上限）
DEFAULT_TOOL_CAPS: Mapping[str, int] = {
    "company_resolver": 1,
    "sec_submissions": 2,
    "sec_company_facts": 1,
    "web_search": 2,
    "filing_downloader": 2,
}


class ToolBudget:
    """每 Job 独立的工具调用硬上限（线程安全，prefetch 与 Agent 调用共用）。"""

    def __init__(self, caps: Mapping[str, int] | None = None) -> None:
        self._caps = dict(caps) if caps is not None else dict(DEFAULT_TOOL_CAPS)
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def cap(self, tool_name: str) -> int | None:
        """返回该工具上限；None 表示无上限。"""
        return self._caps.get(tool_name)

    def try_acquire(self, tool_name: str) -> bool:
        """尝试占用一次执行额度；超限返回 False（不占用）。"""
        cap = self._caps.get(tool_name)
        if cap is None:
            return True
        with self._lock:
            used = self._counts.get(tool_name, 0)
            if used >= cap:
                return False
            self._counts[tool_name] = used + 1
            return True

    def used(self, tool_name: str) -> int:
        """返回该工具已执行次数。"""
        with self._lock:
            return self._counts.get(tool_name, 0)

    def snapshot(self) -> dict[str, int]:
        """返回全部已执行次数（审计/测试用）。"""
        with self._lock:
            return dict(self._counts)
