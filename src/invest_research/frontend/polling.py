"""前端任务轮询服务（P04-UI-03）。

纯逻辑、可独立单测：不依赖 Streamlit。
- ``PollingError``：处理 404（不存在）、503（后端暂不可用）、超时。
- ``poll_until_terminal``：pending/running 时有限轮询，到达终态自动停止；
  未到终态但轮询耗尽 → 停止并返回 ``timed_out=True``。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from invest_research.domain.status import JobStatus
from invest_research.frontend.errors import ApiNotFoundError, ApiTimeoutError
from invest_research.frontend.models import JobSnapshot

__all__ = ["POLL_INTERVAL_SECONDS", "MAX_POLLS", "PollingError", "poll_until_terminal"]

POLL_INTERVAL_SECONDS = 2.0
MAX_POLLS = 60


class PollingError(RuntimeError):
    """轮询期间的后端错误（404 / 503 / 网络不可用），与"任务仍非终态"区分开。"""


def _is_terminal(status: JobStatus) -> bool:
    """Job 终态：succeeded/partial/failed/cancelled。"""
    return status.is_terminal


def poll_until_terminal(
    fetch: Callable[[], JobSnapshot],
    *,
    max_polls: int = MAX_POLLS,
    interval_seconds: float = POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[JobSnapshot, bool]:
    """轮询任务直到终态或耗尽次数。

    返回 ``(snapshot, timed_out)``：``timed_out=False`` 表示已在终态；
    ``timed_out=True`` 表示到达轮询上限仍未终态（UI 提示稍后再查）。

    后端错误（ApiNotFoundError/ApiTimeoutError）转换为 ``PollingError``
    （页面提示"任务不存在"或"超时"），不当作"仍非终态"。
    """
    for _ in range(max_polls):
        snapshot = _safe_fetch(fetch)
        if _is_terminal(snapshot.status):
            return snapshot, False
        if _is_failure_status(snapshot.status):
            # failed/cancelled 也属于终态，直接停止；这里 _is_terminal 已覆盖
            return snapshot, False
        sleep(interval_seconds)
    # 次数耗尽仍未终态
    last = _safe_fetch(fetch)
    return last, not _is_terminal(last.status)


def _is_failure_status(status: JobStatus) -> bool:
    """是否为失败/取消类终态（已含在 is_terminal，保留对称语义）。"""
    return status in {JobStatus.FAILED, JobStatus.CANCELLED}


def _safe_fetch(fetch: Callable[[], JobSnapshot]) -> JobSnapshot:
    """包装 fetch：把后端错误转成 PollingError。"""
    try:
        return fetch()
    except (ApiNotFoundError, ApiTimeoutError) as exc:
        raise PollingError(str(exc)) from exc
