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

from invest_research.domain.status import JobStatus, StepStatus
from invest_research.frontend.errors import ApiClientError
from invest_research.frontend.models import ArtifactInfo, JobListEntry, JobSnapshot

__all__ = [
    "CURRENT_STAGE_LABELS",
    "ERROR_SUGGESTIONS",
    "STATUS_LABELS",
    "STEP_STATUS_ICONS",
    "ArtifactDownloader",
    "JsonArtifactView",
    "current_stage_label",
    "decode_artifact_text",
    "error_suggestion",
    "format_cn_time",
    "is_terminal_status",
    "is_viewable_json_artifact",
    "job_list_row",
    "load_viewable_json_artifacts",
    "profile_badge",
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
    return artifact_key.endswith(".json")


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


class JsonArtifactView(BaseModel):
    """一个可在页面内只读查看的 JSON 中间工件（含原始正文）。"""

    model_config = ConfigDict(frozen=True)

    artifact_key: str
    byte_size: int
    text: str


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


def job_list_row(entry: JobListEntry) -> dict[str, str]:
    """把列表条目转成表格行（不含内部路径/密钥）。"""
    return {
        "公司": entry.input_company,
        "档位": profile_badge(entry.research_profile),
        "状态": status_label(entry.status),
        "创建时间": format_cn_time(entry.created_at),
        "开始时间": format_cn_time(entry.started_at),
        "结束时间": format_cn_time(entry.completed_at),
        "耗时": _format_duration(entry.duration_seconds),
        "当前阶段": current_stage_label(entry.current_step) or "—",
    }


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
        st.metric("当前阶段", stage or "—")

    col4, col5, col6 = st.columns(3)
    with col4:
        st.metric("开始时间", format_cn_time(snapshot.started_at))
    with col5:
        st.metric("结束时间", format_cn_time(snapshot.completed_at))
    with col6:
        st.metric("总耗时", _format_duration(snapshot.duration_seconds))

    # P06-06B：当前阶段醒目文字（只显示真实业务阶段，不显示虚假百分比/ETA）
    if stage is not None and snapshot.status == JobStatus.RUNNING:
        st.markdown(f"### {stage}")

    if snapshot.error_code:
        suggestion = error_suggestion(snapshot.error_code)
        st.warning(f"错误码：{snapshot.error_code} · {snapshot.error_message or ''}")
        if suggestion:
            st.info(suggestion)
    elif snapshot.status == JobStatus.FAILED:
        st.warning("任务失败，请查看下方错误信息。")

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
