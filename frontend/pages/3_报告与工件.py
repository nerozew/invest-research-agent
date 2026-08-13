"""报告、质量结果、引用与工件页面（P04-UI-04）。

功能：
- 输入 job_id，从后端工件清单中读取报告（Markdown）、质量结果、引用与数据限制；
- 展示报告内容（text/markdown）、质量问题、引用和来源、数据限制；
- 下载已登记工件：只能通过 FastAPI 下载接口（后端路径穿越防护）；
- 禁止展示服务器内部路径（storage_uri）与不安全 HTML/JavaScript。

前端只调用 FastAPI（架构 §11），展示层只读。
"""

from __future__ import annotations

import streamlit as st

from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.config import get_api_base_url, get_api_timeout
from invest_research.frontend.errors import ApiClientError

st.set_page_config(page_title="报告与工件", page_icon="📄", layout="wide")
st.title("📄 报告与工件")
st.caption("查看报告、质量结果、引用与已登记工件；下载只通过后端 FastAPI 安全接口进行。")

# 报告相关的 artifact 类型关键字
_REPORT_KEYS = ("report", "quality", "analysis", "research")
_TEXT_KEYS = ("report.md", "report.txt", "quality.md", "analysis.md", "research.md")


def _build_client() -> ResearchApiClient:
    return ResearchApiClient(base_url=get_api_base_url(), timeout=get_api_timeout())


def _render_report_and_artifacts(client: ResearchApiClient, job_id: str) -> None:
    """展示报告文本 + 工件清单；支持安全下载。"""
    try:
        artifacts = client.list_artifacts(job_id)
    except ApiClientError as exc:
        st.error(f"获取工件清单失败：{exc}")
        return

    if not artifacts:
        st.info("该任务暂无已登记工件。")
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
                # 安全渲染：Markdown 允许；不清除脚本内容以防 text/markdown 注入 HTML
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
    job_id = st.text_input("任务 job_id", placeholder="粘贴创建任务后返回的 job_id")
    if not job_id.strip():
        st.info("请输入 job_id 查看报告与工件。")
        return
    _render_report_and_artifacts(_build_client(), job_id.strip())


main()
