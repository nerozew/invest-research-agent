"""任务状态与步骤轮询页面（P04-UI-03）。

功能：
- 输入 job_id 查询任务状态与步骤；
- 展示耗时、当前步骤、错误与尝试次数；
- pending/running 时有限轮询（最多 60 次、间隔 2s），到达终态自动停止；
- 处理 404（任务不存在）、503（服务暂不可用）与超时。

前端只调用 FastAPI（架构 §11），轮询停止条件由前端负责。
"""

from __future__ import annotations

import streamlit as st

from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.config import get_api_base_url, get_api_timeout
from invest_research.frontend.errors import ApiClientError
from invest_research.frontend.models import JobSnapshot, StepSnapshot
from invest_research.frontend.polling import (
    MAX_POLLS,
    POLL_INTERVAL_SECONDS,
    PollingError,
    poll_until_terminal,
)

st.set_page_config(page_title="任务状态", page_icon="🔍", layout="wide")
st.title("🔍 任务状态")
st.caption(
    f"输入任务 job_id 查询进度；任务执行中会自动轮询"
    f"（最多 {MAX_POLLS} 次，间隔 {POLL_INTERVAL_SECONDS:.0f}s），到达终态自动停止。"
)


def _build_client() -> ResearchApiClient:
    return ResearchApiClient(base_url=get_api_base_url(), timeout=get_api_timeout())


def _render_snapshot(snapshot: JobSnapshot) -> None:
    """展示任务总览：状态/耗时/当前步骤/错误。"""
    status_map = {
        "pending": "⏳ 等待中",
        "running": "🔄 执行中",
        "succeeded": "✅ 成功",
        "partial": "⚠️ 部分完成",
        "failed": "❌ 失败",
        "cancelled": "🚫 已取消",
    }
    st.subheader("任务总览")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("状态", status_map.get(snapshot.status.value, snapshot.status.value))
    with col2:
        st.metric(
            "耗时 (秒)",
            snapshot.duration_seconds if snapshot.duration_seconds is not None else "—",
        )
    with col3:
        st.metric("当前步骤", snapshot.current_step or "—")

    if snapshot.error_code:
        st.warning(f"错误码：{snapshot.error_code} · {snapshot.error_message or ''}")

    st.subheader("执行步骤")
    if not snapshot.steps:
        st.info("尚无步骤记录（任务可能刚创建或已被清理）。")
        return
    rows = []
    for step in snapshot.steps:
        rows.append(_step_row(step))
    st.table(rows)


def _step_row(step: StepSnapshot) -> dict[str, str]:
    """把单个步骤快照转成表格行。"""
    return {
        "步骤": str(step.sequence_no),
        "名称": step.step_name,
        "状态": step.status.value,
        "尝试次数": str(step.attempt_count),
        "耗时 (秒)": f"{step.duration_seconds:.3f}" if step.duration_seconds is not None else "—",
        "错误码": step.error_code or "—",
    }


def main() -> None:
    job_id = st.text_input("任务 job_id", placeholder="粘贴创建任务后返回的 job_id")
    if not job_id.strip():
        st.info("请在「创建投研任务」页面创建任务后，把 job_id 粘贴到此处。")
        return

    client = _build_client()

    def fetch() -> JobSnapshot:
        return client.get_research_job(job_id.strip())

    if st.button("查询任务状态", type="primary"):
        with st.spinner("正在查询…"):
            try:
                snapshot, timed_out = poll_until_terminal(fetch)
            except PollingError as exc:
                st.error(f"查询失败：{exc}")
                return
            except ApiClientError as exc:
                st.error(f"查询失败：{exc}")
                return

        _render_snapshot(snapshot)
        if timed_out:
            st.warning(f"已轮询 {MAX_POLLS} 次仍未到达终态，可能任务仍在执行。请稍后再次查询。")


main()
