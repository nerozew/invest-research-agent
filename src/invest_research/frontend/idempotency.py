"""前端 Idempotency-Key 管理器（P04-UI-02）。

架构 §11.3：创建任务使用客户端生成并保存的 ``Idempotency-Key``。

语义（对齐 P04-05 验收）：
- **同一次网络重试复用 Key**：同一请求体（规范化指纹一致）→ 返回同一个 key，
  保证"用户点击创建后网络失败重试"不会重复建任务。
- **真正的新任务生成新 Key**：请求体变化（指纹不一致）→ 生成新 key。

实现：基于"上次请求指纹"缓存。指纹用 ``request.model_dump_json()``
（规范化 JSON 表示，P04-05 后端同款指纹）——同输入必同指纹。
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

__all__ = ["IdempotencyKeyManager", "request_fingerprint"]


def request_fingerprint(request: BaseModel) -> str:
    """请求体规范化指纹：同输入必同指纹（与 P04-05 后端口径一致）。"""
    return request.model_dump_json()


class IdempotencyKeyManager:
    """管理"最近一次请求"对应的 Idempotency-Key。

    - ``key_for(request)``：同一指纹复用上次 key；不同指纹生成新 key。
    - 线程安全不是本前端关注点（Streamlit 单会话串行执行）。
    """

    def __init__(self) -> None:
        self._latest_fingerprint: str | None = None
        self._latest_key: str | None = None

    def key_for(self, request: BaseModel) -> str:
        """返回该请求应使用的 Idempotency-Key。"""
        fingerprint = request_fingerprint(request)
        if fingerprint == self._latest_fingerprint and self._latest_key is not None:
            return self._latest_key
        key = f"ui-{uuid.uuid4()}"
        self._latest_fingerprint = fingerprint
        self._latest_key = key
        return key

    def reset(self) -> None:
        """清空缓存（例如用户显式选择"新建任务"时）。"""
        self._latest_fingerprint = None
        self._latest_key = None
