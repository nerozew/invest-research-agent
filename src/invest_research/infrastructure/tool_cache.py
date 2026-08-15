"""P05.5-3 每 Job 工具调用缓存（ToolCallCache）。

保证「相同工具名 + 规范化参数」在单次 Job 内只执行一次外部 I/O：
- key = f"{tool_name}:{json.dumps(params, sort_keys=True, default=str)}"
- 缓存只存成功结果（失败不缓存，允许底层重试/兜底）；
- 线程安全（prefetch 与 Agent 工具调用可能跨线程）。

依赖边界：仅标准库；禁止导入 CrewAI/httpx/供应商 SDK。
"""

from __future__ import annotations

import json
import threading
from typing import Any


class ToolCallCache:
    """Per-Job 工具结果缓存（工具名 + 规范化参数 → 结果）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, Any] = {}

    @staticmethod
    def normalize_params(params: dict[str, Any]) -> str:
        """规范化参数为稳定字符串（键排序 + JSON；非 JSON 类型经 default=str）。"""
        return json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)

    def key(self, tool_name: str, params: dict[str, Any]) -> str:
        """返回「工具名 + 规范化参数」的缓存键。"""
        return f"{tool_name}:{self.normalize_params(params)}"

    def get(self, key: str) -> Any | None:
        """读取缓存值；未命中返回 None（缓存值本身永不为 None）。"""
        with self._lock:
            return self._store.get(key)

    def put(self, key: str, value: Any) -> None:
        """写入缓存值（调用方只写成功结果）。"""
        with self._lock:
            self._store[key] = value

    def has(self, key: str) -> bool:
        """判断键是否已缓存。"""
        with self._lock:
            return key in self._store
