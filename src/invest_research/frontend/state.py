"""前端任务上下文：job_id 的 URL + session 持久化（P04-UI-08）。

策略：
1. 页面初始化优先读取 URL 中的 job_id（``?job_id=<uuid>``）；
2. URL 无 job_id 时回退 ``session_state``；
3. 两者都没有时由页面展示"最近任务选择器"，不要求用户记忆 UUID；
4. 允许用户手动输入 job_id 作为最后的备用入口；
5. 校验 UUID 格式：非法值显示错误，不调用 API；
6. 绝不在 URL/session 中写入 API Key、数据库地址等敏感信息。

纯逻辑（is_valid_job_id / resolve_job_id）不依赖 Streamlit，可独立单测；
页面通过 load_job_id / save_job_id 使用 st.query_params 与 st.session_state
（Streamlit 1.40+ 正式 API，不用已废弃的实验接口）。
"""

from __future__ import annotations

import uuid

import streamlit as st

__all__ = [
    "JOB_ID_KEY",
    "is_valid_job_id",
    "load_job_id",
    "resolve_job_id",
    "save_job_id",
]

# session_state / query_params 统一的 job_id 键名
JOB_ID_KEY = "job_id"


def is_valid_job_id(value: str | None) -> bool:
    """UUID 格式校验；None/空白/非法返回 False。"""
    if not value or not value.strip():
        return False
    try:
        uuid.UUID(value.strip())
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def resolve_job_id(
    *,
    url_value: str | None,
    session_value: str | None,
    manual_value: str | None,
) -> tuple[str | None, bool]:
    """按优先级解析最终 job_id：URL > session > manual。

    返回 ``(job_id, is_valid)``：
    - ``job_id``：解析出的合法 job_id（不合法返回 None）；
    - ``is_valid``：是否存在一个合法输入（供页面展示错误）。
    """
    for candidate in (url_value, session_value, manual_value):
        if is_valid_job_id(candidate):
            return (candidate or "").strip(), True
    return None, False


def load_job_id() -> str | None:
    """从 URL 或 session_state 读取当前 job_id（URL 优先）。

    非法值返回 None 且不调用 API；页面负责展示可读错误。
    """
    url_value = st.query_params.get(JOB_ID_KEY)
    url_job_id = str(url_value) if url_value is not None else None
    session_job_id = st.session_state.get(JOB_ID_KEY)
    session_value = str(session_job_id) if session_job_id is not None else None
    job_id, _ = resolve_job_id(
        url_value=url_job_id,
        session_value=session_value,
        manual_value=None,
    )
    if job_id is not None and job_id != session_value:
        st.session_state[JOB_ID_KEY] = job_id
    return job_id


def save_job_id(job_id: str) -> None:
    """创建成功后把 job_id 同时写入 session_state 和 URL。"""
    if not is_valid_job_id(job_id):
        raise ValueError("job_id 不是合法 UUID")
    st.session_state[JOB_ID_KEY] = job_id.strip()
    st.query_params[JOB_ID_KEY] = job_id.strip()
