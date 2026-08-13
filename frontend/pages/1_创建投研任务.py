"""创建投研任务页面（P04-UI-02）。

功能：
- 输入公司名称/ticker、as_of_date、语言与表单类型；
- 创建任务时使用客户端 Idempotency-Key（同一次网络重试复用 Key，
  真正的新任务生成新 Key）；
- 展示 job_id 与状态。

前端只调用 FastAPI（架构 §11），不直接访问数据库/Redis/Flow。
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from invest_research.domain.models import ResearchRequest
from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.config import get_api_base_url, get_api_timeout
from invest_research.frontend.errors import ApiClientError, HttpStatusError
from invest_research.frontend.idempotency import IdempotencyKeyManager

st.set_page_config(page_title="创建投研任务", page_icon="+", layout="wide")
st.title("+ 创建投研任务")
st.caption("创建任务使用客户端 Idempotency-Key：同一次网络重试不会重复建任务。")


def _build_client() -> ResearchApiClient:
    return ResearchApiClient(base_url=get_api_base_url(), timeout=get_api_timeout())


def _get_key_manager() -> IdempotencyKeyManager:
    """从 session_state 取/建 IdempotencyKeyManager（不在 session 存密钥，仅存 key 管理器）。"""
    if "idem_manager" not in st.session_state:
        st.session_state["idem_manager"] = IdempotencyKeyManager()
    return st.session_state["idem_manager"]


def _render_form(client: ResearchApiClient, key_manager: IdempotencyKeyManager) -> None:
    """表单：公司/ticker、as_of_date、语言、表单类型。"""
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
        submitted = st.form_submit_button("创建任务", type="primary")

    if not submitted:
        return

    # 构造领域请求（Pydantic 校验：空输入/非法语言/未来日期/空表单在此拦截）
    try:
        request = ResearchRequest(
            input_company=input_company,
            as_of_date=as_of_date,
            language=language,
            requested_forms=tuple(requested_forms),
        )
    except Exception as exc:  # ValidationError 属于输入错误，展示给用户修正
        st.error(f"输入不合法：{exc}")
        return

    # 幂等键：同请求复用，新请求生成新键
    idem_key = key_manager.key_for(request)
    st.caption(f"本次请求 Idempotency-Key：`{idem_key}`")

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

    st.success("任务创建成功！")
    st.json(
        {
            "job_id": str(result.job_id),
            "status": result.status.value,
            "idempotency_key": idem_key,
        }
    )
    st.info("前往「任务状态」页面输入 job_id 查询进度。")


def main() -> None:
    client = _build_client()
    key_manager = _get_key_manager()
    _render_form(client, key_manager)


main()
