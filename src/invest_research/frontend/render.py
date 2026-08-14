"""前端共享展示函数（P04-UI-07/09/10）。

把所有页面都要用的业务状态判断收口在这里，避免把状态判断逻辑复制到多个页面：
- 状态中文标签（含 emoji）
- 终态判断
- failed/partial 的可读建议
- job 快照的页面渲染（任务总览 + 步骤表）
- 任务列表表格行构建

纯展示函数不发起任何网络请求；页面只负责编排 st.* 组件。
"""

from __future__ import annotations

import streamlit as st

from invest_research.domain.status import JobStatus, StepStatus
from invest_research.frontend.models import JobListEntry, JobSnapshot

__all__ = [
    "ERROR_SUGGESTIONS",
    "STATUS_LABELS",
    "error_suggestion",
    "is_terminal_status",
    "job_list_row",
    "render_job_snapshot",
    "status_label",
]

STATUS_LABELS: dict[str, str] = {
    "pending": "⏳ 等待中",
    "running": "🔄 执行中",
    "succeeded": "✅ 成功",
    "partial": "⚠️ 部分完成",
    "failed": "❌ 失败",
    "cancelled": "🚫 已取消",
}

TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.PARTIAL,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}

ERROR_SUGGESTIONS: dict[str, str] = {
    "NETWORK_TRANSIENT": "外部网络暂时不可用，可稍后重试或取消后重新创建。",
    "LLM_PARSE": "模型输出无法解析，已按重试策略处理；持续失败请降低任务复杂度。",
    "VALIDATION": "输入或中间产物校验未通过，请检查公司名称与截止日期。",
    "DOWNLOAD": "文档下载失败，可稍后重试。",
    "UNKNOWN": "发生未知错误，请查看错误信息并重试。",
}


def status_label(status: JobStatus | str) -> str:
    """把 JobStatus 转成中文标签（未知值回退原值）。"""
    key = status.value if isinstance(status, JobStatus) else str(status)
    return STATUS_LABELS.get(key, key)


def is_terminal_status(status: JobStatus | str) -> bool:
    """判断任务是否已到终态（succeeded/partial/failed/cancelled）。"""
    actual = status if isinstance(status, JobStatus) else JobStatus(status)
    return actual in TERMINAL_STATUSES


def error_suggestion(error_code: str | None) -> str | None:
    """根据错误码给出可读建议；无错误码返回 None。"""
    if not error_code:
        return None
    return ERROR_SUGGESTIONS.get(error_code, ERROR_SUGGESTIONS["UNKNOWN"])


def _step_status_label(step_status: StepStatus | str) -> str:
    """Step 状态中文标签。"""
    labels = {
        "pending": "等待中",
        "running": "执行中",
        "succeeded": "成功",
        "failed_retryable": "失败(可重试)",
        "failed_terminal": "失败(终态)",
        "skipped": "跳过",
    }
    key = step_status.value if isinstance(step_status, StepStatus) else str(step_status)
    return labels.get(key, key)


def job_list_row(entry: JobListEntry) -> dict[str, str]:
    """把列表条目转成表格行（不含内部路径/密钥）。"""
    created = entry.created_at.strftime("%Y-%m-%d %H:%M") if entry.created_at else "—"
    return {
        "公司": entry.input_company,
        "状态": status_label(entry.status),
        "创建时间": created,
        "当前步骤": entry.current_step or "—",
    }


def render_job_snapshot(snapshot: JobSnapshot) -> None:
    """渲染任务总览 + 步骤表（P04-UI-03/09/10 共用，避免复制判断逻辑）。"""
    st.subheader("任务总览")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("状态", status_label(snapshot.status))
    with col2:
        st.metric(
            "耗时 (秒)",
            snapshot.duration_seconds if snapshot.duration_seconds is not None else "—",
        )
    with col3:
        st.metric("当前步骤", snapshot.current_step or "—")

    if snapshot.error_code:
        suggestion = error_suggestion(snapshot.error_code)
        st.warning(f"错误码：{snapshot.error_code} · {snapshot.error_message or ''}")
        if suggestion:
            st.info(suggestion)
    elif snapshot.status == JobStatus.FAILED:
        st.warning("任务失败，请查看下方错误信息。")

    st.subheader("执行步骤")
    if not snapshot.steps:
        st.info("尚无步骤记录（任务可能刚创建或已被清理）。")
        return
    rows = []
    for step in snapshot.steps:
        rows.append(
            {
                "步骤": str(step.sequence_no),
                "名称": step.step_name,
                "状态": _step_status_label(step.status),
                "尝试次数": str(step.attempt_count),
                "耗时 (秒)": (
                    f"{step.duration_seconds:.3f}" if step.duration_seconds is not None else "—"
                ),
                "错误码": step.error_code or "—",
            }
        )
    st.table(rows)
