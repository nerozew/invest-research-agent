"""P05.5-fix 预取结果契约（PrefetchResult）。

把公司身份确认后的并行预取结果打包为结构化对象，供 LiveResearchFlowRunner
注入 Research Task（Agent 不再需要重新猜公司/日期/查询参数；缓存仍保留，
但结果必须显式告知 Agent）。

依赖边界：仅标准库 + domain；禁止导入 CrewAI/httpx/供应商 SDK。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from invest_research.domain.models import CompanyIdentity

PrefetchStatus = Literal["ok", "partial", "failed"]


@dataclass(frozen=True)
class PrefetchResult:
    """公司身份确认后的预取结果（注入 Research Task 的摘要）。"""

    company_identity: CompanyIdentity | None
    submissions_summary: str | None  # SEC 申报摘要（多行文本）
    search_summary: str | None  # Serper 搜索摘要（多行文本）
    status: PrefetchStatus  # ok / partial / failed


def prefetch_summary_text(result: PrefetchResult | None) -> str:
    """把预取结果压缩为 Research Task 可直接使用的一小段文本。"""
    if result is None or result.company_identity is None:
        return "无预取结果（需先用 CompanyResolver 解析公司）"
    identity = result.company_identity
    lines = [
        f"company_identity: ticker={identity.ticker}, CIK={identity.cik}, "
        f"legal_name={identity.legal_name}, exchange={identity.exchange}"
    ]
    if result.submissions_summary:
        lines.append("[SEC submissions]")
        lines.append(result.submissions_summary)
    if result.search_summary:
        lines.append("[Serper search]")
        lines.append(result.search_summary)
    if result.status == "partial":
        lines.append("（部分预取未成功，可补充调用对应工具）")
    return "\n".join(lines)
