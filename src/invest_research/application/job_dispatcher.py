"""任务创建成功后投递到后台队列的端口（P04-10A）。

语义：
- 仅在「任务确实首次创建成功」（数据库提交后）才投递 job_id 到队列；
- 幂等复用旧 job_id 时不得重复投递；
- production 实现使用 Celery/Redis；测试注入 fake dispatcher 完全离线。

MVP 不引入 transactional outbox；「数据库成功但消息投递失败」的窗口
作为已知限制记录（见 learning log / README）。
"""

from __future__ import annotations

import uuid
from typing import Protocol

__all__ = ["JobDispatcher"]


class JobDispatcher(Protocol):
    """把等待后台处理的 job_id 投递到队列的端口。

    实现者负责：投递失败时抛出可分类异常；调用方决定是否阻断响应。
    """

    def dispatch(self, job_id: uuid.UUID) -> None: ...
