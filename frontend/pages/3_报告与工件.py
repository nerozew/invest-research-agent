"""报告、质量结果、引用与工件页面（P04-UI-04 + P04-UI-08/10 + P06-07 前置修复）。

功能：
- 从 URL/session 自动恢复当前 job_id（P04-UI-08）；刷新/复制 URL 可恢复；
- 展示报告文本、质量结果、引用与工件清单；只通过 FastAPI 下载（后端路径穿越防护）；
- P06-07 前置修复：最终报告（08_report.md / 09_report.pdf）提供清晰的
  查看/下载入口——Markdown 直接在页面渲染预览，PDF 提供下载按钮；
  JSON 中间产物只在"已登记工件"表中展示，不作为最终用户报告。
- succeeded 但没有 artifact 时显示"当前任务尚无可下载工件"，不报错；
- 提供返回任务中心与查看任务详情导航。

前端只调用 FastAPI（架构 §11），展示层只读。
"""

from __future__ import annotations

import streamlit as st

from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.config import get_api_base_url, get_api_timeout
from invest_research.frontend.errors import ApiClientError
from invest_research.frontend.models import ArtifactInfo
from invest_research.frontend.render import artifact_category, load_viewable_json_artifacts
from invest_research.frontend.state import load_job_id

st.set_page_config(page_title="报告与工件", page_icon="📄", layout="wide")
st.title("📄 报告与工件")
st.caption(
    "查看最终报告（Markdown/PDF）、质量结果与已登记工件；"
    "下载只通过后端 FastAPI 安全接口进行。"
)

# P06-07 前置修复：最终报告工件类型与文件名关键字
_FINAL_REPORT_MD_TYPE = "final_report_markdown"
_FINAL_REPORT_PDF_TYPE = "final_report_pdf"
_FINAL_REPORT_MD_KEYS = ("08_report.md", "report.md")
_FINAL_REPORT_PDF_KEYS = ("09_report.pdf", "report.pdf")
# 旧报告相关类型（兼容展示，不再作为唯一入口）
_LEGACY_REPORT_KEYS = ("quality", "analysis", "research")


def _build_client() -> ResearchApiClient:
    return ResearchApiClient(base_url=get_api_base_url(), timeout=get_api_timeout())


@st.cache_resource
def _cached_client() -> ResearchApiClient:
    return _build_client()


def _is_final_md(artifact_type: str, artifact_key: str) -> bool:
    return artifact_type == _FINAL_REPORT_MD_TYPE or artifact_key in _FINAL_REPORT_MD_KEYS


def _is_final_pdf(artifact_type: str, artifact_key: str) -> bool:
    return artifact_type == _FINAL_REPORT_PDF_TYPE or artifact_key in _FINAL_REPORT_PDF_KEYS


def _render_final_report_section(
    client: ResearchApiClient, job_id: str, artifacts: list[ArtifactInfo]
) -> None:
    """渲染最终报告专属区域：Markdown 预览 + PDF 下载（P06-07 前置修复）。"""
    md_artifact = next(
        (a for a in artifacts if _is_final_md(a.artifact_type, a.artifact_key)), None
    )
    pdf_artifact = next(
        (a for a in artifacts if _is_final_pdf(a.artifact_type, a.artifact_key)), None
    )

    if md_artifact is None and pdf_artifact is None:
        return

    st.subheader("📑 最终报告")
    if md_artifact is not None:
        st.markdown(
            f"**Markdown 报告**（`{md_artifact.artifact_key}`，"
            f"{md_artifact.byte_size} 字节）"
        )
        try:
            md_bytes = client.download_artifact(job_id, md_artifact.artifact_key)
            text = md_bytes.decode("utf-8", errors="replace")
        except ApiClientError as exc:
            st.warning(f"无法读取 {md_artifact.artifact_key}：{exc}")
        else:
            st.markdown(text)
            st.download_button(
                label="⬇️ 下载 Markdown 报告",
                data=md_bytes,
                file_name=md_artifact.artifact_key,
                mime="text/markdown",
                type="primary",
                key="download_final_md",
            )
    if pdf_artifact is not None:
        st.markdown(f"**PDF 报告**（`{pdf_artifact.artifact_key}`，{pdf_artifact.byte_size} 字节）")
        try:
            pdf_bytes = client.download_artifact(job_id, pdf_artifact.artifact_key)
        except ApiClientError as exc:
            st.warning(f"无法读取 {pdf_artifact.artifact_key}：{exc}")
        else:
            st.download_button(
                label="⬇️ 下载 PDF 报告",
                data=pdf_bytes,
                file_name=pdf_artifact.artifact_key,
                mime="application/pdf",
                type="primary",
                key="download_final_pdf",
            )
            st.caption("PDF 报告可直接下载后用 PDF 阅读器打开；浏览器不支持页内 PDF 预览。")


def _render_artifact_table(
    client: ResearchApiClient, job_id: str, artifacts: list[ArtifactInfo]
) -> None:
    """展示已登记工件清单；JSON 中间产物支持页面内只读查看原始代码。

    - 00~07 中间工件（``artifact_key`` 以 ``.json`` 结尾）用 ``st.expander``
      折叠展示原始 JSON（方案 B：零状态、零点击、天然可折叠）；
    - ``08_report.md`` / ``09_report.pdf`` 与 ``st.txt`` 等非 JSON 工件
      继续保持下载/文本展示逻辑，不进入 JSON 查看；
    - 下载失败只显示 ``st.warning``，不中断整页（与最终报告区域一致）。
    """
    st.subheader("已登记工件")
    st.caption("年度任务按证据、财务比较、章节产物和运行状态分类；原始 SEC 文件不会自动读取。")
    # 只展示 key/type/size，不展示 storage_uri（服务器内部路径）
    st.table(
        [
            {
                "key": a.artifact_key,
                "类别": artifact_category(a.artifact_key, a.artifact_type),
                "类型": a.artifact_type,
                "大小 (字节)": a.byte_size,
            }
            for a in artifacts
        ]
    )

    # P06-11I-FRONTEND：JSON 中间产物页面内只读查看（点名称即展开原始 JSON）
    views, errors = load_viewable_json_artifacts(client, job_id, artifacts)
    for view in views:
        with st.expander(f"查看 {view.artifact_key}（{view.byte_size} 字节）"):
            st.code(view.text, language="json")
    for artifact_key, error in errors:
        st.warning(f"无法读取 {artifact_key}：{error}")

    # 兼容旧版：旧报告相关的纯文本（st.txt 等）仍允许查看（不作为唯一入口）；
    # 旧 .json 类型已由上方统一 JSON 查看区域覆盖，避免重复展示。
    for artifact in artifacts:
        if (
            artifact.artifact_type in _LEGACY_REPORT_KEYS
            and artifact.artifact_key.endswith(".txt")
        ):
            st.subheader(f"中间报告：{artifact.artifact_key}")
            try:
                content = client.download_artifact(job_id, artifact.artifact_key)
                text = content.decode("utf-8", errors="replace")
            except ApiClientError as exc:
                st.warning(f"无法读取 {artifact.artifact_key}：{exc}")
                continue
            st.code(text, language="text")


def _render_report_and_artifacts(client: ResearchApiClient, job_id: str) -> None:
    """展示最终报告 + 工件清单；支持安全下载。"""
    try:
        artifacts = client.list_artifacts(job_id)
    except ApiClientError as exc:
        st.error(f"获取工件清单失败：{exc}")
        return

    if not artifacts:
        st.info("当前任务尚无可下载工件。")
        return

    _render_final_report_section(client, job_id, artifacts)
    _render_artifact_table(client, job_id, artifacts)


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
