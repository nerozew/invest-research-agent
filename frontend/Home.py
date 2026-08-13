"""Streamlit 多页面应用 —— 首页（连接状态）。

本页只调用 FastAPI 的 /health 与 /readiness（P04-UI-01：typed API client
验收），不直接访问数据库/Redis/Flow，也不读取服务器工件路径。

运行方式：
    streamlit run frontend/Home.py
"""

from __future__ import annotations

import streamlit as st

from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.config import get_api_base_url, get_api_timeout
from invest_research.frontend.errors import ApiClientError

st.set_page_config(page_title="自动化投研系统", page_icon="📊", layout="wide")

st.title("📊 Agent 驱动的自动化投研系统")
st.caption("本界面是 FastAPI 的 HTTP 客户端（架构文档 §11）：所有数据都经后端 API 获取。")


def _build_client() -> ResearchApiClient:
    """依据环境变量构建 typed API client。"""
    return ResearchApiClient(base_url=get_api_base_url(), timeout=get_api_timeout())


@st.cache_resource
def _cached_client() -> ResearchApiClient:
    """缓存 client 实例（同一 API 地址下复用连接池）。"""
    return _build_client()


def render_connection_status() -> None:
    """展示后端连接状态：/health（存活）与 /readiness（依赖就绪）。"""
    client = _cached_client()
    st.subheader("后端连接状态")

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


def main() -> None:
    st.subheader("功能导航")
    st.markdown(
        """
        - **创建投研任务**：输入公司名称/ticker 创建研究任务（使用 Idempotency-Key）。
        - **任务状态**：查询任务与步骤的实时状态、耗时与错误。
        - **报告与工件**：查看报告、质量结果、引用与已登记工件。
        """
    )
    st.divider()
    render_connection_status()


main()
