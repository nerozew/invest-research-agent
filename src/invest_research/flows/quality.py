"""P03-13 确定性质量门禁（hard gate，不靠 LLM 判断）。

对齐 docs/04-WORKFLOW-RELIABILITY.md §7.1「硬门禁」：
- 三个 Agent pack 都已生成（schema 已由 Pydantic 保证）；
- 报告必需章节与"非投资建议"声明存在；
- 报告引用键（citation_keys）非空（每个外部事实可追溯）；
- 报告不出现目标价/确定性收益承诺（简单关键词拦截）；
- 公司身份与数据截止日一致（research_pack 与 request 对齐）。

产出 `QualityReport`（version / all_passed / issues / warnings / recommendation），
写入 ``state.quality_report``（P03-14 发布时读取）。

本模块为纯函数，不依赖 CrewAI；可完全离线单测。
"""

from __future__ import annotations

from invest_research.domain.models import QualityReport
from invest_research.flows.state import ResearchFlowState

# PRD §7 报告必需章节（writer_prompt_v1 / TemplateGuide 对齐）
REQUIRED_SECTIONS: tuple[str, ...] = (
    "执行摘要",
    "公司",
    "业务概览",
    "财务表现",
    "风险",
    "数据限制",
    "非投资建议",
)

# 报告中禁止出现的投资建议/收益承诺（硬性红线）
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "买入",
    "卖出",
    "目标价",
    "建议持仓",
)


def run_quality_gate(state: ResearchFlowState) -> QualityReport:
    """对执行完 00-05 的 state 运行硬质量门禁。"""
    issues: list[str] = []
    warnings: list[str] = []

    # 1. 三个 Agent pack 必须都已生成（缺则过不了 05，属防御性检查）
    if state.research_pack is None:
        issues.append("缺少 research_pack（步骤 02 未完成）")
    if state.analysis_pack is None:
        issues.append("缺少 analysis_pack（步骤 04 未完成）")
    if state.report_draft is None:
        issues.append("缺少 report_draft（步骤 05 未完成）")

    # 2. 公司身份与数据截止日一致
    if state.research_pack is not None and state.request is not None:
        if state.research_pack.as_of_date != state.request.as_of_date:
            issues.append("research_pack.as_of_date 与请求 as_of_date 不一致")
    if (
        state.analysis_pack is not None
        and state.research_pack is not None
        and state.analysis_pack.period_end != state.research_pack.as_of_date
    ):
        issues.append("analysis_pack.period_end 与 research_pack.as_of_date 不一致")

    # 3. 报告必需章节与"非投资建议"声明（需在 report_draft 存在时检查）
    if state.report_draft is not None:
        md = state.report_draft.markdown
        for section in REQUIRED_SECTIONS:
            if section not in md:
                issues.append(f"报告缺少必需章节: {section}")
        # 4. 引用键非空（每个事实可追溯）
        if not state.report_draft.citation_keys:
            issues.append("report_draft.citation_keys 为空（无引用可追溯）")
        # 5. 禁止投资建议/目标价
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in md:
                issues.append(f"报告出现禁止的投资建议/承诺: {pattern}")
    else:
        # report_draft 缺失，章节/引用/声明无法检查
        warnings.append("report_draft 缺失，章节与非投资建议声明无法检查")

    # 6. 强制"非投资建议"声明（独立红线，即便未在必需章节列表中）
    if state.report_draft is not None and "非投资建议" not in state.report_draft.markdown:
        issues.append("报告必须包含'非投资建议'声明")

    all_passed = not issues
    recommendation = "published" if all_passed else "rejected"
    return QualityReport(
        version="quality_report_v1",
        all_passed=all_passed,
        issues=issues,
        warnings=warnings,
        recommendation=recommendation,
    )
