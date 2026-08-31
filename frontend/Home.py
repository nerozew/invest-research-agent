"""Streamlit 多页面应用 —— 首页：任务中心（P04-UI-07 + 时区/耗时增强）。

功能：
- 只调用 FastAPI 的 GET /v1/research-jobs 获取最近任务（架构 §11）；
- 展示公司、状态（中文标签）、创建/开始/结束时间（中国时区简化格式）、耗时、当前步骤；
- 支持按状态筛选与下一页/上一页（cursor 分页）；
- 空列表显示明确空状态；API 错误显示可读错误 + 重试按钮；
- 每一行提供"查看详情"导航；
- 另附系统健康状态（/health 与 /readiness）。

前端只通过 HTTP 访问业务能力。
"""

from __future__ import annotations

import streamlit as st

from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.config import get_api_base_url, get_api_timeout
from invest_research.frontend.errors import ApiClientError
from invest_research.frontend.models import JobListEntry, JobListPage
from invest_research.frontend.render import STATUS_LABELS, format_cn_time, job_list_row

st.set_page_config(page_title="自动化投研系统", page_icon="📊", layout="wide")

st.title("📊 Agent 驱动的自动化投研系统")
st.caption("任务中心：所有数据都经后端 FastAPI 获取，不直接访问数据库。")


def _build_client() -> ResearchApiClient:
    return ResearchApiClient(base_url=get_api_base_url(), timeout=get_api_timeout())


@st.cache_resource
def _cached_client() -> ResearchApiClient:
    """缓存 client 实例（同一 API 地址下复用连接池）。"""
    return _build_client()


def _render_connection_status(client: ResearchApiClient) -> None:
    """展示后端连接状态：/health（存活）与 /readiness（依赖就绪）。"""
    st.subheader("系统健康状态")
    with st.spinner("正在检查后端连接…"):
        try:
            health = client.health()
            st.success(f"Liveness OK —— 服务：{health.service}")
        except ApiClientError as exc:
            st.error(f"无法连接后端 API（{exc}）")
            st.info("请确认 FastAPI 已启动，且 API_BASE_URL 配置正确。")
            return

        try:
            readiness = client.readiness()
        except ApiClientError as exc:
            st.error(f"Readiness 检查失败（{exc}）")
            return

        if readiness.ready:
            st.success("Readiness OK —— PostgreSQL 与 Redis 均就绪")
        else:
            st.warning("Readiness 未就绪 —— 部分依赖不可用")
        col_db, col_redis = st.columns(2)
        with col_db:
            st.metric("PostgreSQL", readiness.database.status)
        with col_redis:
            st.metric("Redis", readiness.redis.status)


def _render_job_table(client: ResearchApiClient) -> None:
    """最近任务：筛选 + 分页 + 查看详情。"""
    st.subheader("最近任务")

    status_options = ["全部"] + [
        "pending",
        "running",
        "succeeded",
        "partial",
        "failed",
        "cancelled",
    ]
    selected_status = st.selectbox(
        "按状态筛选",
        options=status_options,
        index=0,
        key="recent_jobs_status_filter",
    )
    status_filter = None if selected_status == "全部" else selected_status

    cursor_key = "recent_jobs_cursor"
    cursor = st.session_state.get(cursor_key)

    try:
        page: JobListPage = client.list_jobs(limit=10, status=status_filter, cursor=cursor)
    except ApiClientError as exc:
        st.error(f"获取任务列表失败：{exc}")
        if st.button("重试", key="recent_jobs_retry"):
            st.session_state.pop(cursor_key, None)
            st.rerun()
        return

    if not page.items:
        st.info("暂无可展示的任务。请先「创建投研任务」。")
        st.session_state.pop(cursor_key, None)
        return

    st.table([job_list_row(e) for e in page.items])

    col_prev, col_next, col_pos = st.columns([1, 1, 2])
    has_prev = cursor is not None
    with col_prev:
        if st.button("◀ 上一页", key="recent_jobs_prev", disabled=not has_prev):
            st.session_state.pop(cursor_key, None)
            st.rerun()
    with col_next:
        has_next = page.next_cursor is not None
        if st.button("下一页 ▶", key="recent_jobs_next", disabled=not has_next):
            st.session_state[cursor_key] = page.next_cursor
            st.rerun()
    with col_pos:
        if has_next:
            st.caption("还有更多任务…")
        elif has_prev and not has_next:
            st.caption("已是最后一页")

    st.markdown("#### 查看任务详情")
    selected = st.selectbox(
        "选择一个任务",
        options=page.items,
        format_func=lambda e: (
            f"{e.input_company} · "
            f"{STATUS_LABELS.get(e.status.value, e.status.value)} · "
            f"{format_cn_time(e.created_at)}"
        ),
        key="recent_jobs_select",
    )
    if st.button("查看详情", type="primary", key="recent_jobs_view"):
        _select_job(selected)


def _select_job(entry: JobListEntry) -> None:
    """选中任务：写入 session + URL 后进入详情页。"""
    from invest_research.frontend.state import save_job_id

    save_job_id(str(entry.job_id))
    st.switch_page("pages/2_任务状态.py")


def main() -> None:
    client = _cached_client()
    st.button(
        "➕ 创建投研任务",
        type="primary",
        key="home_create_job",
        on_click=lambda: st.switch_page("pages/1_创建投研任务.py"),
    )
    _render_job_table(client)
    st.divider()
    _render_connection_status(client)


main()
