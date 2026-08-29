"""前端共享展示函数（P04-UI-07/09/10 + P06-06A/B + 时区/耗时增强）。

- 状态中文标签（含 emoji）、终态判断、错误建议
- job 快照渲染（任务总览 + 步骤表）、任务列表行构建
- P06-06A：档位徽章；P06-06B：当前阶段中文、步骤图标、旧任务兼容
- P06-06B 收口：中国时区（Asia/Shanghai）简化时间格式（26-08-16 18:21），
  列表/详情展示开始、结束、耗时（后端已提供 started_at/completed_at/duration_seconds）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

import streamlit as st
from pydantic import BaseModel, ConfigDict

from invest_research.domain.annual_node_runtime import AnnualNodeGraphSnapshot
from invest_research.domain.annual_pipeline import (
    ResearchMode,
    ResearchNodeKind,
    ResearchNodeStatus,
)
from invest_research.domain.status import JobStatus, StepStatus
from invest_research.frontend.errors import ApiClientError
from invest_research.frontend.models import (
    ArtifactInfo,
    JobListEntry,
    JobSnapshot,
    PerformanceSnapshot,
)

__all__ = [
    "CURRENT_STAGE_LABELS",
    "ERROR_SUGGESTIONS",
    "STATUS_LABELS",
    "STEP_STATUS_ICONS",
    "ArtifactDownloader",
    "ANNUAL_NODE_KIND_LABELS",
    "ANNUAL_NODE_STATUS_LABELS",
    "DiagnosticsDownloader",
    "JsonArtifactView",
    "annual_event_rows",
    "annual_node_rows",
    "annual_wait_rows",
    "artifact_category",
    "current_stage_label",
    "decode_artifact_text",
    "error_suggestion",
    "format_cn_time",
    "is_terminal_status",
    "is_viewable_json_artifact",
    "job_list_row",
    "load_viewable_json_artifacts",
    "profile_badge",
    "research_mode_badge",
    "render_failed_diagnostics",
    "render_job_snapshot",
    "status_label",
]

_CN_TZ = ZoneInfo("Asia/Shanghai")
_CN_TIME_FMT = "%y-%m-%d %H:%M"

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

# P06-06B：current_step 名称 → 当前阶段中文文案。
CURRENT_STAGE_LABELS: dict[str, str] = {
    "00_request": "正在初始化任务",
    "01_company_resolve": "正在解析公司",
    "02_research": "正在搜索 SEC 与公开资料",
    "03_documents": "正在下载和解析财报",
    "04_analysis": "正在分析财务数据",
    "05_writer": "正在撰写报告",
    "06_quality_gate": "正在检查报告质量",
    "07_manifest": "正在生成最终工件",
}

# P06-06B：步骤状态 → 紧凑图标。
STEP_STATUS_ICONS: dict[str, str] = {
    "pending": "⏳ 等待",
    "running": "🔄 进行中",
    "succeeded": "✅ 完成",
    "failed_retryable": "❌ 失败",
    "failed_terminal": "❌ 失败",
    "skipped": "⏭ 跳过",
}

# P06-06A：研究档位 → 徽章文本。
PROFILE_BADGES: dict[str, str] = {
    "fast": "⚡ 快速",
    "deep": "🔬 深度",
}

# P07-12：年度模式与节点快照只展示稳定、可解释的脱敏字段。
RESEARCH_MODE_BADGES: dict[str, str] = {
    "legacy": "🧭 常规研究",
    "annual_deep": "📅 年度深度研究",
}

ANNUAL_NODE_KIND_LABELS: dict[str, str] = {
    ResearchNodeKind.DISCOVER_ANNUAL_FILINGS.value: "发现年度文件",
    ResearchNodeKind.FETCH_COMPANY_FACTS.value: "获取 Company Facts",
    ResearchNodeKind.DOWNLOAD_FILING.value: "下载 Filing",
    ResearchNodeKind.PARSE_FILING.value: "解析 Filing",
    ResearchNodeKind.VALIDATE_EVIDENCE.value: "校验证据",
    ResearchNodeKind.BUILD_ANNUAL_COMPARISON.value: "构建年度财务比较",
    ResearchNodeKind.REQUEST_SUPPLEMENT.value: "受控补证",
    ResearchNodeKind.ANALYZE_FINANCIALS.value: "财务归因分析",
    ResearchNodeKind.WRITE_SECTION.value: "撰写章节",
    ResearchNodeKind.FINALIZE_REPORT.value: "最终门禁与发布",
}

ANNUAL_NODE_STATUS_LABELS: dict[str, str] = {
    ResearchNodeStatus.PENDING.value: "⏳ 等待",
    ResearchNodeStatus.RUNNING.value: "🔄 进行中",
    ResearchNodeStatus.SUCCEEDED.value: "✅ 完成",
    ResearchNodeStatus.FAILED_RETRYABLE.value: "🔁 可重试失败",
    ResearchNodeStatus.FAILED_TERMINAL.value: "❌ 终态失败",
    ResearchNodeStatus.BLOCKED.value: "🚧 已阻塞",
    ResearchNodeStatus.CANCELLED.value: "🚫 已取消",
}

ANNUAL_NODE_KEY_LABELS: dict[str, str] = {
    "annual_selection": "选择目标/上年 10-K",
    "annual_evidence_fanout": "并发获取年度证据",
    "annual_comparison": "确定性财务比较",
    "annual_route_evidence": "证据权限路由",
    "annual_section_plan": "生成章节计划",
    "annual_financial_analysis": "财务归因分析",
    "annual_write_financial": "撰写财务章节",
    "annual_write_business": "撰写业务章节",
    "annual_write_risk": "撰写风险章节",
    "annual_write_events": "撰写重大事件章节",
    "annual_finalization": "最终质量门禁",
    "annual_final_writer": "组装最终报告",
}


def format_cn_time(value: datetime | None) -> str:
    """把后端时间转为中国时区并简化展示（``26-08-16 18:21``）。

    后端存 UTC（timezone-aware）；前端统一转 Asia/Shanghai。
    缺失/未开始返回 "—"。
    """
    if value is None:
        return "—"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(_CN_TZ).strftime(_CN_TIME_FMT)


def _format_duration(seconds: float | None) -> str:
    """把耗时秒数转成可读文本（如 12.3s / 2分05秒）；缺失返回 "—"。"""
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}分{secs:02d}秒"


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


def profile_badge(research_profile: str | None) -> str:
    """把档位名转成徽章文本；旧响应缺字段时回退 deep。"""
    if not research_profile:
        return PROFILE_BADGES["deep"]
    return PROFILE_BADGES.get(research_profile, research_profile)


def research_mode_badge(research_mode: ResearchMode | str | None) -> str:
    """把运行模式映射为用户可读的徽章；旧响应缺字段时按 legacy 展示。"""
    key = research_mode.value if isinstance(research_mode, ResearchMode) else research_mode
    return RESEARCH_MODE_BADGES.get(key or ResearchMode.LEGACY.value, str(key or "legacy"))


def current_stage_label(current_step: str | None) -> str | None:
    """把 current_step 名称转成当前阶段中文文案；无 current_step 返回 None。"""
    if not current_step:
        return None
    return CURRENT_STAGE_LABELS.get(current_step, current_step)


def is_viewable_json_artifact(artifact_key: str) -> bool:
    """判断工件是否可在页面内查看 JSON 原始代码。

    - 只有以 ``.json`` 结尾的 00~07 中间工件开放页内只读查看；
    - ``08_report.md`` / ``09_report.pdf`` 与 ``st.txt`` 等非 JSON
      工件继续保持下载/文本展示逻辑，不进入 JSON 查看（只读、不上传）。
    """
    normalized = artifact_key.lower()
    # 年度原始 SEC 文件和全文解析结果可能很大，也不属于前端诊断面板的最小权限范围。
    # 它们仍可通过受控工件接口按需下载，但页面不会自动请求或展示正文。
    if normalized.startswith("annual/") and (
        "/source." in normalized or normalized.endswith("/parsed.json")
    ):
        return False
    # 保持既有契约：仅小写 .json 才允许页内查看。
    return artifact_key.endswith(".json")


def artifact_category(artifact_key: str, artifact_type: str) -> str:
    """按年度工件键给用户可读分类；未知内容安全回退为通用工件。"""
    key = artifact_key.lower()
    if artifact_type in {"final_report_markdown", "final_report_pdf"} or key.endswith(
        ("report.md", "report.pdf")
    ):
        return "最终报告"
    if key == "annual/comparison.json":
        return "财务比较"
    if key.startswith("annual/sections/"):
        return "章节产物"
    if key == "annual/runtime_state.json":
        return "年度运行状态"
    if key.startswith("annual/company-facts/") or key.startswith("annual/"):
        return "年度证据"
    return "通用工件"


def decode_artifact_text(content: bytes) -> str:
    """把工件字节解码为可展示文本（UTF-8，非法字节以替换符代替）。

    与 ``_render_final_report_section`` 的容错方式一致：解码失败时
    不做大小写/编码猜测，直接 ``errors="replace"`` 保证页面不中断。
    """
    return content.decode("utf-8", errors="replace")


class ArtifactDownloader(Protocol):
    """只读下载器端口：页面与测试只需提供 ``download_artifact``。"""

    def download_artifact(self, job_id: str, artifact_key: str) -> bytes:
        """下载已登记工件字节（后端路径穿越防护）。"""


class DiagnosticsDownloader(Protocol):
    """脱敏诊断包下载端口：页面与测试只需提供 ``download_diagnostics_bundle``。"""

    def download_diagnostics_bundle(self, job_id: str) -> bytes:
        """下载脱敏诊断包（.tar.gz 字节，仅本地调试）。"""


class JsonArtifactView(BaseModel):
    """一个可在页面内只读查看的 JSON 中间工件（含原始正文）。"""

    model_config = ConfigDict(frozen=True)

    artifact_key: str
    byte_size: int
    text: str


def build_diagnostics_event_rows(
    events: list[dict[str, object]] | None,
) -> list[dict[str, str]]:
    """P06-11K-4：把执行事件压缩为最近 10 条的表格行（纯函数，可离线测试）。

    只提取展示白名单字段（时间/阶段/类型/摘要类型/状态），绝不暴露 payload
    正文、密钥或内部路径。超过 10 条只保留最后 10 条。
    """
    rows: list[dict[str, str]] = []
    for ev in (events or [])[-10:]:
        rows.append(
            {
                "时间": str(ev.get("timestamp") or ""),
                "阶段": str(ev.get("stage") or ""),
                "类型": str(ev.get("event_type") or ""),
                "摘要类型": str(ev.get("payload_kind") or ""),
                # 稳定错误码优先（错误码是任务/步骤级稳定分类，status 是状态桶）。
                "状态/错误码": str(ev.get("error_code") or ev.get("status") or ""),
            }
        )
    return rows


def render_failed_diagnostics(
    client: DiagnosticsDownloader,
    job_id: str,
    *,
    error_code: str | None,
    failure_stage: str | None,
    recent_events: list[dict[str, object]] | None = None,
) -> None:
    """P06-11K-4：失败任务诊断入口（错误阶段/稳定错误码/最近 10 条事件/下载按钮）。

    - 只显示稳定错误码与错误阶段（不展示内部路径/密钥）；
    - 最近 10 条执行事件以表格展示（失败时后端才提供；缺省空列表）；
    - 「下载脱敏诊断包」按钮只通过后端 FastAPI 下载（前端不存密钥）；
    - 紧邻按钮提供「诊断包可能包含业务输入，仅供本地调试」提示。
    """
    st.subheader("🧪 失败诊断")

    col_code, col_stage = st.columns(2)
    with col_code:
        st.metric("错误阶段", failure_stage or "—")
    with col_stage:
        st.metric("稳定错误码", error_code or "—")

    rows = build_diagnostics_event_rows(recent_events)
    if rows:
        st.markdown("**最近 10 条执行事件**")
        st.table(rows)
    else:
        st.info("暂无执行事件（任务未失败或诊断捕获未启用）。")

    try:
        archive_bytes = client.download_diagnostics_bundle(job_id)
    except ApiClientError as exc:
        st.warning(f"诊断包不可用：{exc}")
        return

    st.download_button(
        label="⬇️ 下载脱敏诊断包",
        data=archive_bytes,
        file_name=f"diagnostics-{job_id}.tar.gz",
        mime="application/gzip",
        type="secondary",
        key=f"download_diagnostics_{job_id}",
    )
    st.caption("⚠️ 诊断包可能包含业务输入（公司名/查询词），仅供本地调试，请勿对外分享。")


def load_viewable_json_artifacts(
    client: ArtifactDownloader,
    job_id: str,
    artifacts: list[ArtifactInfo],
) -> tuple[list[JsonArtifactView], list[tuple[str, str]]]:
    """下载并解码所有可查看的 JSON 中间工件。

    - 只处理 ``is_viewable_json_artifact`` 为真的 `.json` 工件；
    - 单个工件下载失败（``ApiClientError``）收集为 ``(key, 错误消息)``，
      不中断其他工件的加载（页面据此显示 ``st.warning``）；
    - 非 JSON 工件（08_report.md / 09_report.pdf / st.txt 等）一律跳过，
      保持下载/文本展示逻辑不变，不进入 JSON 查看。
    """
    views: list[JsonArtifactView] = []
    errors: list[tuple[str, str]] = []
    for artifact in artifacts:
        if not is_viewable_json_artifact(artifact.artifact_key):
            continue
        try:
            content = client.download_artifact(job_id, artifact.artifact_key)
        except ApiClientError as exc:
            errors.append((artifact.artifact_key, str(exc)))
            continue
        views.append(
            JsonArtifactView(
                artifact_key=artifact.artifact_key,
                byte_size=artifact.byte_size,
                text=decode_artifact_text(content),
            )
        )
    return views, errors


def _step_status_icon(step_status: StepStatus | str) -> str:
    """步骤状态紧凑图标（未知值回退原值）。"""
    key = step_status.value if isinstance(step_status, StepStatus) else str(step_status)
    return STEP_STATUS_ICONS.get(key, key)


def _annual_node_label(node_key: str) -> str:
    return ANNUAL_NODE_KEY_LABELS.get(node_key, node_key)


def _annual_node_kind_label(kind: ResearchNodeKind | str) -> str:
    key = kind.value if isinstance(kind, ResearchNodeKind) else str(kind)
    return ANNUAL_NODE_KIND_LABELS.get(key, key)


def _annual_node_status_label(status: ResearchNodeStatus | str | None) -> str:
    if status is None:
        return "—"
    key = status.value if isinstance(status, ResearchNodeStatus) else str(status)
    return ANNUAL_NODE_STATUS_LABELS.get(key, key)


def annual_node_rows(graph: AnnualNodeGraphSnapshot) -> list[dict[str, str]]:
    """构造年度节点表，仅投影诊断快照白名单字段。"""
    return [
        {
            "节点": _annual_node_label(node.node_key),
            "类型": _annual_node_kind_label(node.kind),
            "状态": _annual_node_status_label(node.status),
            "尝试": f"{node.attempt_count}/{node.max_attempts}",
            "产出工件": "、".join(node.output_artifact_keys) or "—",
            "阻塞/错误": node.blocked_reason or node.error_code or "—",
        }
        for node in graph.nodes
    ]


def annual_wait_rows(graph: AnnualNodeGraphSnapshot) -> list[dict[str, str]]:
    """构造等待原因表，不泄露上游输入、提示词或原始 SEC 内容。"""
    return [
        {
            "等待节点": _annual_node_label(item.node_key),
            "未完成上游": _annual_node_label(item.upstream_node_key),
            "上游状态": _annual_node_status_label(item.upstream_status),
            "原因": item.reason_code,
        }
        for item in graph.waiting
    ]


def annual_event_rows(graph: AnnualNodeGraphSnapshot) -> list[dict[str, str]]:
    """构造最近年度事件表，严格使用事件快照的白名单字段。"""
    return [
        {
            "时间": format_cn_time(event.created_at),
            "节点": _annual_node_label(event.node_key),
            "事件": event.event_type.value,
            "状态": (
                f"{_annual_node_status_label(event.previous_status)} → "
                f"{_annual_node_status_label(event.new_status)}"
                if event.previous_status is not None or event.new_status is not None
                else "—"
            ),
            "原因": event.reason_code or "—",
            "尝试": str(event.attempt_count),
            "工件": "、".join(event.artifact_keys) or "—",
        }
        for event in graph.recent_events
    ]


def job_list_row(entry: JobListEntry) -> dict[str, str]:
    """把列表条目转成表格行（不含内部路径/密钥）。"""
    return {
        "公司": entry.input_company,
        "模式": research_mode_badge(entry.research_mode),
        "档位": profile_badge(entry.research_profile),
        "状态": status_label(entry.status),
        "创建时间": format_cn_time(entry.created_at),
        "开始时间": format_cn_time(entry.started_at),
        "结束时间": format_cn_time(entry.completed_at),
        "耗时": _format_duration(entry.duration_seconds),
        "当前阶段": (
            "年度 DAG 执行中"
            if entry.research_mode is ResearchMode.ANNUAL_DEEP and entry.current_step is None
            else current_stage_label(entry.current_step) or "—"
        ),
    }


def _render_annual_node_snapshot(graph: AnnualNodeGraphSnapshot | None) -> None:
    """渲染年度 DAG 的节点、等待和预算诊断，不读取任何工件正文。"""
    st.subheader("年度执行节点")
    if graph is None:
        st.info("年度节点图正在初始化；下次轮询将显示节点、依赖等待原因和关键路径。")
        return

    by_status = {status: 0 for status in ResearchNodeStatus}
    for node in graph.nodes:
        by_status[node.status] += 1
    col_done, col_active, col_problem, col_path = st.columns(4)
    with col_done:
        st.metric("已完成节点", by_status[ResearchNodeStatus.SUCCEEDED])
    with col_active:
        st.metric(
            "等待/执行中",
            by_status[ResearchNodeStatus.PENDING] + by_status[ResearchNodeStatus.RUNNING],
        )
    with col_problem:
        st.metric(
            "失败/阻塞",
            by_status[ResearchNodeStatus.FAILED_RETRYABLE]
            + by_status[ResearchNodeStatus.FAILED_TERMINAL]
            + by_status[ResearchNodeStatus.BLOCKED],
        )
    with col_path:
        st.metric("关键路径", _format_duration(graph.critical_path_seconds))

    if graph.critical_path_node_keys:
        path = " → ".join(_annual_node_label(key) for key in graph.critical_path_node_keys)
        st.caption(f"关键路径：{path}")

    st.table(annual_node_rows(graph))
    waits = annual_wait_rows(graph)
    if waits:
        st.markdown("**依赖等待原因**")
        st.table(waits)
    if graph.budget_stop_reasons:
        st.warning("补证/预算停止原因：" + "、".join(graph.budget_stop_reasons))
    events = annual_event_rows(graph)
    if events:
        st.markdown("**最近节点事件**")
        st.table(events)


def _fmt_int(value: int | None) -> str:
    """千分位整数；缺失显示 —（不伪造）。"""
    return f"{value:,}" if value is not None else "—"


def _render_performance_snapshot(performance: PerformanceSnapshot | None) -> None:
    """WS3：per-job 成本与用量（缺失字段显示 —，不伪造）。"""
    if performance is None:
        return
    st.subheader("成本与用量")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("LLM 调用", _fmt_int(performance.llm_calls))
    with c2:
        st.metric("总 Token", _fmt_int(performance.total_tokens))
    with c3:
        cost = performance.estimated_cost_usd
        st.metric("估算成本 (USD)", f"${cost:.4f}" if cost is not None else "—")
    c4, c5, c6 = st.columns(3)
    with c4:
        st.metric("输入 Token", _fmt_int(performance.input_tokens))
    with c5:
        st.metric("输出 Token", _fmt_int(performance.output_tokens))
    with c6:
        st.metric("工具调用", _fmt_int(performance.tool_calls_total))


def render_job_snapshot(snapshot: JobSnapshot) -> None:
    """渲染任务总览 + 步骤表（任务/详情共用）。"""
    st.subheader("任务总览")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("状态", status_label(snapshot.status))
    with col2:
        st.metric("档位", profile_badge(snapshot.research_profile))
    with col3:
        stage = current_stage_label(snapshot.current_step)
        if snapshot.research_mode is ResearchMode.ANNUAL_DEEP:
            st.metric("模式", research_mode_badge(snapshot.research_mode))
        else:
            st.metric("当前阶段", stage or "—")

    col4, col5, col6 = st.columns(3)
    with col4:
        st.metric("开始时间", format_cn_time(snapshot.started_at))
    with col5:
        st.metric("结束时间", format_cn_time(snapshot.completed_at))
    with col6:
        st.metric("总耗时", _format_duration(snapshot.duration_seconds))

    # P06-06B：当前阶段醒目文字（只显示真实业务阶段，不显示虚假百分比/ETA）
    if (
        snapshot.research_mode is ResearchMode.LEGACY
        and stage is not None
        and snapshot.status == JobStatus.RUNNING
    ):
        st.markdown(f"### {stage}")

    if snapshot.error_code:
        suggestion = error_suggestion(snapshot.error_code)
        st.warning(f"错误码：{snapshot.error_code} · {snapshot.error_message or ''}")
        if suggestion:
            st.info(suggestion)
    elif snapshot.status == JobStatus.FAILED:
        st.warning("任务失败，请查看下方错误信息。")

    _render_performance_snapshot(snapshot.performance)

    if snapshot.research_mode is ResearchMode.ANNUAL_DEEP:
        _render_annual_node_snapshot(snapshot.annual_nodes)
        return

    st.subheader("执行步骤")
    if not snapshot.steps:
        # P06-06B：旧任务（steps 为空）显示明确兼容文案
        st.info("该任务使用旧版执行记录，暂无详细步骤。")
        return
    rows = []
    for step in snapshot.steps:
        rows.append(
            {
                "步骤": str(step.sequence_no),
                "名称": step.step_name,
                "状态": _step_status_icon(step.status),
                "尝试次数": str(step.attempt_count),
                "耗时": _format_duration(step.duration_seconds),
                "错误码": step.error_code or "—",
            }
        )
    st.table(rows)
