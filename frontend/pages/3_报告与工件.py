"""报告、质量结果、引用与工件页面（P04-UI-04 + P04-UI-08/10）。

功能：
- 从 URL/session 自动恢复当前 job_id（P04-UI-08）；刷新/复制 URL 可恢复；
- 展示报告文本、质量结果、引用与工件清单；只通过 FastAPI 下载（后端路径穿越防护）；
- succeeded 但没有 artifact 时显示"当前任务尚无可下载工件"，不报错；
- 提供返回任务中心与查看任务详情导航。

前端只调用 FastAPI（架构 §11），展示层只读。
"""

from __future__ import annotations

import streamlit as st

from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.config import get_api_base_url, get_api_timeout
from invest_research.frontend.errors import ApiClientError
from invest_research.frontend.state import load_job_id

st.set_page_config(page_title="报告与工件", page_icon="📄", layout="wide")
st.title("📄 报告与工件")
st.caption("查看报告、质量结果、引用与已登记工件；下载只通过后端 FastAPI 安全接口进行。")

# 报告相关的 artifact 类型关键字
_REPORT_KEYS = ("report", "quality", "analysis", "research")
_TEXT_KEYS = ("report.md", "report.txt", "quality.md", "analysis.md", "research.md")


def _build_client() -> ResearchApiClient:
    return ResearchApiClient(base_url=get_api_base_url(), timeout=get_api_timeout())


@st.cache_resource
def _cached_client() -> ResearchApiClient:
    return _build_client()


def _render_report_and_artifacts(client: ResearchApiClient, job_id: str) -> None:
    """展示报告文本 + 工件清单；支持安全下载。"""
    try:
        artifacts = client.list_artifacts(job_id)
    except ApiClientError as exc:
        st.error(f"获取工件清单失败：{exc}")
        return

    if not artifacts:
        st.info("当前任务尚无可下载工件。")
        return

    st.subheader("已登记工件")
    # 只展示 key/type/size，不展示 storage_uri（服务器内部路径）
    st.table(
        [
            {
                "key": a.artifact_key,
                "类型": a.artifact_type,
                "大小 (字节)": a.byte_size,
            }
            for a in artifacts
        ]
    )

    # 尝试展示报告内容（纯文本/Markdown，安全渲染）
    for artifact in artifacts:
        if artifact.artifact_type in _REPORT_KEYS or artifact.artifact_key in _TEXT_KEYS:
            if artifact.artifact_key.endswith((".md", ".txt", ".json")):
                st.subheader(f"报告：{artifact.artifact_key}")
                try:
                    content = client.download_artifact(job_id, artifact.artifact_key)
                    text = content.decode("utf-8", errors="replace")
                except ApiClientError as exc:
                    st.warning(f"无法读取 {artifact.artifact_key}：{exc}")
                    continue
                if artifact.artifact_key.endswith(".md"):
                    st.markdown(text)
                else:
                    st.code(
                        text, language="text" if artifact.artifact_key.endswith(".txt") else "json"
                    )

    # 下载按钮：只允许已登记工件
    st.subheader("下载工件")
    selected = st.selectbox("选择要下载的工件", options=[a.artifact_key for a in artifacts])
    if st.button("下载所选工件", type="primary"):
        try:
            data = client.download_artifact(job_id, selected)
        except ApiClientError as exc:
            st.error(f"下载失败：{exc}")
            return
        st.download_button(
            label=f"下载 {selected} ({len(data)} 字节)",
            data=data,
            file_name=selected,
            mime="application/octet-stream",
        )


def main() -> None:
    col_back, col_detail = st.columns(2)
    with col_back:
        if st.button("← 返回任务中心", key="report_back_home"):
            st.switch_page("Home.py")
    with col_detail:
        if st.button("🔍 查看任务详情", key="report_view_detail"):
            st.switch_page("pages/2_任务状态.py")

    job_id = load_job_id()
    if job_id is None:
        st.info("请先在「创建投研任务」页面创建任务，或从任务中心选择任务。")
        return

    st.caption(f"当前任务：`{job_id}`")
    _render_report_and_artifacts(_cached_client(), job_id)


main()
