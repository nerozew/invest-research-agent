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
from invest_research.flows.quality_classifier import (
    all_passed_from_issues,
    classify_state,
    recommendation_from_issues,
)
from invest_research.flows.state import ResearchFlowState


def run_quality_gate(state: ResearchFlowState) -> QualityReport:
    """对执行完 00-05 的 state 运行确定性硬质量门禁（P03-17 结构化分类版）。

    - 用 ``quality_classifier.classify_state`` 生成结构化 QualityIssue（发布/修订/拒绝）；
    - 回填 ``QualityReport``（issues 为消息、warnings 为警告、recommendation 为枚举），
      保持既有布尔 ``all_passed`` 语义：无 ERROR/CRITICAL 即可发布（含 partial）。
    """
    q_issues = classify_state(state)
    issues = [i.message for i in q_issues]
    warnings: list[str] = []
    if state.report_draft is None:
        warnings.append("report_draft 缺失，章节与非投资建议声明无法检查")

    return QualityReport(
        version="quality_report_v1",
        all_passed=all_passed_from_issues(q_issues),
        issues=issues,
        warnings=warnings,
        recommendation=recommendation_from_issues(q_issues, warnings),
    )
