"""创建投研任务页面（P04-UI-10 + P06-06A 档位选择）。

功能：
- 输入公司名称/ticker、as_of_date、语言与表单类型；
- P06-06A：增加 fast/deep 研究档位单选（UI 默认 fast，显式传值给后端）；
- 使用 st.form 避免普通 rerun 重复提交；
- 创建后把 job_id 写入 session_state + URL（P04-UI-08），
  刷新/复制 URL 后可恢复；
- 提供复制 job_id、查看任务详情、查看报告与工件、返回最近任务；
- 展示公司、job_id、档位与状态。

前端只调用 FastAPI，不直接访问数据库/Redis/Flow。
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from invest_research.domain.models import ResearchRequest
from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.config import get_api_base_url, get_api_timeout
from invest_research.frontend.errors import ApiClientError, HttpStatusError
from invest_research.frontend.idempotency import IdempotencyKeyManager
from invest_research.frontend.state import save_job_id

st.set_page_config(page_title="创建投研任务", page_icon="➕", layout="wide")
st.title("➕ 创建投研任务")
st.caption("创建任务使用客户端 Idempotency-Key：同一次网络重试不会重复建任务。")


def _build_client() -> ResearchApiClient:
    return ResearchApiClient(base_url=get_api_base_url(), timeout=get_api_timeout())


def _get_key_manager() -> IdempotencyKeyManager:
    """从 session_state 取/建 IdempotencyKeyManager（不存密钥，只存 key 管理器）。"""
    if "idem_manager" in st.session_state:
        manager = st.session_state["idem_manager"]
        if isinstance(manager, IdempotencyKeyManager):
            return manager
    manager = IdempotencyKeyManager()
    st.session_state["idem_manager"] = manager
    return manager


def _render_profile_badge(research_profile: str) -> str:
    """把档位名转成徽章文本（fast → ⚡ 快速；deep → 🔬 深度；未知回退原文）。"""
    return {"fast": "⚡ 快速", "deep": "🔬 深度"}.get(research_profile, research_profile)


def _render_success_nav(job_id: str, company: str, research_profile: str) -> None:
    """创建成功后的连贯导航：复制 / 详情 / 报告 / 返回。"""
    st.success("任务创建成功！")
    st.info(
        f"**公司**：{company} ｜ **job_id**：`{job_id}` ｜ "
        f"**档位**：{_render_profile_badge(research_profile)} ｜ **状态**：⏳ 等待中"
    )
    st.code(job_id, language=None)

    col_copy, col_detail, col_report, col_back = st.columns(4)
    with col_copy:
        st.button(
            "📋 复制 job_id",
            key="create_copy_job_id",
            on_click=lambda: st.write("已复制（请手动 Ctrl+C 上面的 job_id）"),
        )
    with col_detail:
        if st.button("查看任务详情", type="primary", key="create_view_detail"):
            st.switch_page("pages/2_任务状态.py")
    with col_report:
        if st.button("查看报告与工件", key="create_view_report"):
            st.switch_page("pages/3_报告与工件.py")
    with col_back:
        if st.button("返回最近任务", key="create_back_home"):
            st.switch_page("Home.py")


def _render_form(client: ResearchApiClient, key_manager: IdempotencyKeyManager) -> None:
    """表单：公司/ticker、as_of_date、语言、表单类型、研究档位（st.form 防重复提交）。"""
    with st.form("create_research_job_form"):
        input_company = st.text_input(
            "公司名称或股票代码", placeholder="例如 Microsoft、AAPL、苹果公司"
        )
        as_of_date = st.date_input(
            "数据截止日 (as of date)", value=date.today(), max_value=date.today()
        )
        language = st.selectbox("报告语言", options=["zh-CN", "en"])
        requested_forms = st.multiselect(
            "请求的 SEC 表单类型",
            options=["10-K", "10-Q", "10-K/A", "10-Q/A"],
            default=["10-K", "10-Q"],
        )
        # P06-06A：每任务研究档位选择。UI 默认 fast（推荐）；显式传值给后端 ResearchRequest。
        research_profile = st.radio(
            "研究档位",
            options=["fast", "deep"],
            index=0,
            format_func=lambda v: (
                "快速模式（推荐）：耗时和费用较低，适合初步研究与演示"
                if v == "fast"
                else "深度模式：研究更充分，但耗时和费用更高"
            ),
            key="create_research_profile",
        )
        submitted = st.form_submit_button("创建任务", type="primary")

    if not submitted:
        return

    try:
        request = ResearchRequest(
            input_company=input_company,
            as_of_date=as_of_date,
            language=language,
            requested_forms=tuple(requested_forms),
            # P06-06A：前端显式传递用户所选档位（不依赖领域默认 deep）
            research_profile=research_profile,
        )
    except Exception as exc:  # ValidationError 是输入错误，展示给用户修正
        st.error(f"输入不合法：{exc}")
        return

    # 幂等键：同请求复用，新请求生成新键；一次提交生命周期内稳定
    idem_key = key_manager.key_for(request)

    with st.spinner("正在创建任务…"):
        try:
            result = client.create_research_job(request=request, idempotency_key=idem_key)
        except HttpStatusError as exc:
            if exc.status_code == 409:
                st.error("Idempotency-Key 冲突：同一 key 已被不同的请求体使用，请刷新页面重试。")
            else:
                st.error(f"创建任务失败（HTTP {exc.status_code}）：{exc.detail}")
            return
        except ApiClientError as exc:
            st.error(f"创建任务失败：{exc}")
            return

    job_id = str(result.job_id)
    # P04-UI-08：创建成功后持久化 job_id 到 session + URL，自动进入任务上下文
    save_job_id(job_id)
    _render_success_nav(job_id, input_company, research_profile)


def main() -> None:
    client = _build_client()
    key_manager = _get_key_manager()
    if st.button("← 返回任务中心", key="create_back_home_top"):
        st.switch_page("Home.py")
    _render_form(client, key_manager)


main()
