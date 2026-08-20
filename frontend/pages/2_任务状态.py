"""任务状态与步骤轮询页面（P04-UI-08/09/10）。

功能：
- 从 URL/session 自动恢复当前 job_id（P04-UI-08）；无 job_id 时提供
  最近任务选择 + 手动输入备用入口；
- 状态区域用 ``st.fragment(run_every=...)`` 局部轮询（P04-UI-09）：
  只刷新任务状态/步骤/耗时，页面标题与导航不反复重建；
- pending/running 时轮询，终态（succeeded/partial/failed/cancelled）停止；
- 网络暂时失败提示并允许下次轮询恢复；
- 取消任务后刷新详情状态；
- failed/partial 展示 error_code、error_message 与可读建议（共享展示函数）；
- 提供返回任务中心与查看报告/工件。

前端只调用 FastAPI（架构 §11），轮询停止条件由前端负责。
"""

from __future__ import annotations

import streamlit as st

from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.config import get_api_base_url, get_api_timeout
from invest_research.frontend.errors import ApiClientError
from invest_research.frontend.render import (
    is_terminal_status,
    render_failed_diagnostics,
    render_job_snapshot,
)
from invest_research.frontend.state import load_job_id, save_job_id

st.set_page_config(page_title="任务状态", page_icon="🔍", layout="wide")
st.title("🔍 任务状态")
st.caption("任务执行中会自动局部轮询（每 2~3 秒），到达终态自动停止。")


def _build_client() -> ResearchApiClient:
    return ResearchApiClient(base_url=get_api_base_url(), timeout=get_api_timeout())


@st.cache_resource
def _cached_client() -> ResearchApiClient:
    return _build_client()


def _choose_job_id() -> str | None:
    """无 job_id 时的选择器：最近任务选择 + 手动输入备用入口。"""
    client = _cached_client()

    try:
        page = client.list_jobs(limit=10)
    except ApiClientError as exc:
        st.warning(f"无法加载最近任务：{exc}")
        page = None

    if page is not None and page.items:
        selected = st.selectbox(
            "选择最近任务",
            options=page.items,
            format_func=lambda e: f"{e.input_company} · {e.status.value}",
            key="status_job_picker",
        )
        if st.button("使用该任务", key="status_use_picked"):
            save_job_id(str(selected.job_id))
            st.rerun()
            return str(selected.job_id)

    manual = st.text_input(
        "或手动输入 job_id（备用入口）",
        placeholder="粘贴 UUID",
        key="status_manual_job_id",
    )
    if st.button("查询", key="status_manual_go") and manual.strip():
        from invest_research.frontend.state import is_valid_job_id

        if not is_valid_job_id(manual):
            st.error("job_id 不是合法 UUID，无法查询。")
            return None
        save_job_id(manual.strip())
        st.rerun()
        return manual.strip()
    return None


def _cancel_job(client: ResearchApiClient, job_id: str) -> None:
    """取消任务：协作式取消；成功/已终态都刷新详情。"""
    try:
        result = client.cancel_research_job(job_id)
    except ApiClientError as exc:
        st.error(f"取消失败：{exc}")
        return
    if result.did_cancel:
        st.success("已请求取消该任务。")
    elif result.already_cancelled:
        st.info("该任务已处于取消/终态。")
    st.rerun()


def _render_status_fragment(client: ResearchApiClient, job_id: str) -> None:
    """局部轮询 fragment：只刷新任务状态/步骤/耗时区域（P04-UI-09）。"""
    try:
        snapshot = client.get_research_job(job_id)
    except ApiClientError as exc:
        st.error(f"查询失败：{exc}")
        st.info("网络暂时失败，将在下次自动轮询时重试。")
        # 允许下次轮询恢复：run_every 会再次调用本函数
        return

    if is_terminal_status(snapshot.status):
        render_job_snapshot(snapshot)
        st.success("任务已到达终态，已停止自动轮询。")
        # P06-11K-4：失败/部分完成 → 失败诊断入口（错误阶段/错误码/下载诊断包）。
        if snapshot.status.value in ("failed", "partial"):
            render_failed_diagnostics(
                client,
                str(snapshot.job_id),
                error_code=snapshot.error_code,
                failure_stage=snapshot.failure_stage,
                recent_events=None,
            )
        return

    render_job_snapshot(snapshot)
    # 非终态：提供取消入口
    if snapshot.status.value in ("pending", "running"):
        if st.button("🚫 取消任务", key="status_cancel_job"):
            _cancel_job(client, job_id)
            return

    st.info(f"任务执行中（{snapshot.status.value}），每 2~3 秒自动刷新状态…")


def _fragment_status(client: ResearchApiClient, job_id: str) -> None:
    st.fragment(
        lambda: _render_status_fragment(client, job_id),
        run_every=2.0,
    )


def main() -> None:
    if st.button("← 返回任务中心", key="status_back_home"):
        st.switch_page("Home.py")

    job_id = load_job_id()
    if job_id is None:
        st.subheader("选择要查看的任务")
        picked = _choose_job_id()
        if picked:
            job_id = picked
        else:
            st.info("请从最近任务中选择，或手动输入 job_id。")
            return

    client = _cached_client()
    st.caption(f"当前任务：`{job_id}`")

    if st.button("📄 查看报告与工件", key="status_view_report"):
        st.switch_page("pages/3_报告与工件.py")

    _fragment_status(client, job_id)


main()
