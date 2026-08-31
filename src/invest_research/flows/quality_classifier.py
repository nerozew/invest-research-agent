"""P03-17 结构化质量问题分类（quality classifier，确定性）。

把质量门禁升级为「QualityIssue 列表 + QualityRecommendation」的分类，
供 P03-18/19/20 定向修订/补证/受控路由使用。纯函数，不依赖 CrewAI。

P06-11F 变更：
- 禁止项检测从「简单子串匹配」改为「确定性上下文规则」：
  真实建议（建议买入/建议卖出/目标价为 X/应当持仓）必须拒绝；
  「本报告不构成买入、卖出或目标价建议」等免责声明不被误判；
- 空值与不完整结果统一走 ``empty_value_policy``（集中式策略，
  不把空值判断散落在多个 if 中）；
- 合法 partial/unavailable → PUBLISH_PARTIAL；complete 缺核心事实 → REJECT；
- 报告使用外部事实但 citation_keys 为空 → 仍 REVISE（不无条件放宽）。
"""

from __future__ import annotations

import re

from invest_research.application.empty_value_policy import (
    CompletenessVerdict,
    check_analysis_completeness,
)
from invest_research.domain.models import AnalysisCompleteness
from invest_research.domain.quality import (
    QualityAction,
    QualityIssue,
    QualityRecommendation,
    QualitySeverity,
)
from invest_research.flows.state import ResearchFlowState

REQUIRED_SECTIONS: tuple[str, ...] = (
    "执行摘要",
    "公司与业务概览",
    "财务表现",
    "风险",
    "数据限制",
    "非投资建议",
)

# P06-11F：不再使用裸关键词子串匹配。禁止项必须命中"肯定建议"上下文，
# 免责声明（"不构成/不建议/并非…买入/卖出/目标价"）不误判。
_ADVICE_REJECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 明确的建议句式：建议买入 / 建议卖出 / 建议增持 / 建议减持
    re.compile(r"建议\s*(买入|卖出|增持|减持|清仓|持有)"),
    # 应当/应该持仓
    re.compile(r"(应当|应该|应)\s*持仓"),
    # 目标价 / 目标价格 + 数值（含 $ / ￥ / 纯数字 / 带单位）
    re.compile(r"目标价(格)?\s*(为|是|：|:)?\s*[$￥]?\s*\d[\d,]*(\.\d+)?"),
    # 确定性收益承诺（"将上涨 X%" / "保证收益"）
    re.compile(r"(将|会|必然|保证)\s*上涨\s*\d"),
    re.compile(r"确定性收益|保证收益|承诺收益|稳赚"),
    # "买入评级" / "卖出评级"（机构评级语义，属建议）
    re.compile(r"(买入|卖出|增持|减持|持有)\s*评级"),
)

# 免责声明否定词：出现在建议关键词前 N 字符内视为免责语境（放行）。
_DISCLAIMER_NEGATIONS: tuple[str, ...] = (
    "不构成",
    "不提供",
    "不建议",
    "并非",
    "不表示",
    "不代表",
    "非投资建议",
    "不应当",
    "不应被解读",
)
_NEGATION_WINDOW = 40  # 否定词与关键词之间的最大字符距离


def _is_disclaimer_context(markdown: str, key_start: int) -> bool:
    """判断关键词位置是否处于免责声明否定语境（确定性字符窗口）。

    - 回溯到最近的句子分隔符（。！？；\n）之后，取同句文本；
    - 仅当同句内（或句子开头前 N 字符内）出现否定词才算免责语境；
    - 避免"本句建议买入，后一句不构成…"误放行。
    """
    before = markdown[max(0, key_start - _NEGATION_WINDOW) : key_start]
    # 取同句起始（最近句子分隔符之后），保证否定词与建议在同一句语义中。
    sentence_start = max(
        before.rfind("。"),
        before.rfind("！"),
        before.rfind("？"),
        before.rfind("；"),
        before.rfind("\n"),
    )
    window = before if sentence_start == -1 else before[sentence_start + 1 :]
    return any(neg in window for neg in _DISCLAIMER_NEGATIONS)


def find_forbidden_advice(markdown: str) -> list[str]:
    """确定性检测报告中的真实投资建议（返回命中的稳定短语列表）。

    - 对每个拒绝模式做正则匹配；
    - 命中位置若处于免责声明否定语境（如"本报告不构成买入、卖出或
      目标价建议"），则**不放行**——该位置之前的否定词说明这是免责声明；
    - 仅当命中位置**不在**否定语境时才算真实建议（拒绝）。
    注意：免责声明必须保留在报告中，不能靠删除关键词绕过。
    """
    hits: list[str] = []
    for pattern in _ADVICE_REJECT_PATTERNS:
        for match in pattern.finditer(markdown):
            if _is_disclaimer_context(markdown, match.start()):
                continue
            phrase = match.group(0)
            if phrase not in hits:
                hits.append(phrase)
    return hits


def _issue(
    code: str,
    severity: QualitySeverity,
    stage: str,
    message: str,
    action: QualityAction,
) -> QualityIssue:
    return QualityIssue(code=code, severity=severity, stage=stage, message=message, action=action)


def classify_state(state: ResearchFlowState) -> list[QualityIssue]:
    """对 00-05 产物输出结构化 QualityIssue（确定性）。"""
    issues: list[QualityIssue] = []

    if state.research_pack is None:
        issues.append(
            _issue(
                "missing_research_pack",
                QualitySeverity.CRITICAL,
                "research",
                "缺少 research_pack",
                QualityAction.REJECT,
            )
        )
    if state.analysis_pack is None:
        issues.append(
            _issue(
                "missing_analysis_pack",
                QualitySeverity.CRITICAL,
                "analysis",
                "缺少 analysis_pack",
                QualityAction.REJECT,
            )
        )
    # P06-09A：unavailable 状态不得包含任何财务数据（防御直接构造 state 绕过
    # Pydantic 校验的路径；正常解析已由模型层保证）。
    if (
        state.analysis_pack is not None
        and getattr(state.analysis_pack, "schema_version", None) == "analysis_pack_v2"
        and state.analysis_pack.completeness == AnalysisCompleteness.UNAVAILABLE
        and (state.analysis_pack.facts or state.analysis_pack.metrics)
    ):
        issues.append(
            _issue(
                "unavailable_with_content",
                QualitySeverity.CRITICAL,
                "analysis",
                "completeness=unavailable 但包含 facts/metrics（不得伪造财务数据）",
                QualityAction.REJECT,
            )
        )

    # P06-11F：统一空值/完整性策略（代替散落的 if 判断）。
    # 仅对 v2 schema 应用新语义；v1 旧工件保持宽松兼容（对齐 Pydantic 模型
    # 自身的跨字段校验：v1 不做新状态强制约束）。
    if (
        state.analysis_pack is not None
        and getattr(state.analysis_pack, "schema_version", None) == "analysis_pack_v2"
    ):
        verdict = check_analysis_completeness(
            completeness=str(state.analysis_pack.completeness.value),
            facts=list(state.analysis_pack.facts),
            metrics=list(state.analysis_pack.metrics),
            limitations=list(state.analysis_pack.limitations),
            unavailable_reason=state.analysis_pack.unavailable_reason,
        )
        if verdict.verdict == CompletenessVerdict.REJECT:
            issues.append(
                _issue(
                    "incomplete_analysis_state",
                    QualitySeverity.CRITICAL,
                    "analysis",
                    verdict.issues[0],
                    QualityAction.REJECT,
                )
            )

    if state.report_draft is None:
        issues.append(
            _issue(
                "missing_report_draft",
                QualitySeverity.CRITICAL,
                "writer",
                "缺少 report_draft",
                QualityAction.REJECT,
            )
        )

    if (
        state.research_pack is not None
        and state.request is not None
        and state.research_pack.as_of_date != state.request.as_of_date
    ):
        issues.append(
            _issue(
                "as_of_mismatch",
                QualitySeverity.CRITICAL,
                "research",
                "as_of_date 不一致",
                QualityAction.REJECT,
            )
        )
    if (
        state.analysis_pack is not None
        and state.research_pack is not None
        and state.analysis_pack.period_end > state.research_pack.as_of_date
    ):
        # P05.5-fix：分析的是财年/季度期间，period_end 是期间截止日（如 AAPL FY2025
        # 截至 2025-09-27），只需不晚于数据截止日 as_of_date（禁止使用截止日之后数据），
        # 不应与 as_of_date 精确相等。
        issues.append(
            _issue(
                "period_end_mismatch",
                QualitySeverity.CRITICAL,
                "analysis",
                "period_end 晚于 as_of_date（禁止使用截止日之后的数据）",
                QualityAction.REJECT,
            )
        )

    if state.report_draft is not None:
        md = state.report_draft.markdown
        for section in REQUIRED_SECTIONS:
            if section not in md:
                issues.append(
                    _issue(
                        "missing_section",
                        QualitySeverity.ERROR,
                        "writer",
                        f"缺少必需章节: {section}",
                        QualityAction.REVISE_REPORT,
                    )
                )
        # P06-11F：citation_keys 不无条件放宽。报告引用了外部事实（research_pack
        # 有可信来源 或 analysis_pack 有 facts）但 citation_keys 为空 → REVISE。
        # 报告只有数据不可用说明时（analysis_pack.completeness=unavailable），
        # 允许按实际来源决定是否把 missing_citation_keys 降级为 warning。
        uses_external_facts = bool(
            (state.research_pack is not None and state.research_pack.sources)
            or (state.analysis_pack is not None and state.analysis_pack.facts)
        )
        if not state.report_draft.citation_keys:
            if uses_external_facts:
                issues.append(
                    _issue(
                        "missing_citation_keys",
                        QualitySeverity.ERROR,
                        "writer",
                        "citation_keys 为空（报告引用外部事实时必须存在合法 citation key）",
                        QualityAction.REVISE_REPORT,
                    )
                )
            # 报告只有数据不可用说明时：不无条件 REVISE，由调用方决定。
        # P06-11F：非法 citation key（不在注册表）必须拒绝。注册表由调用方
        # 注入 state（见 Flow 层）；未注入时跳过该项（历史路径向后兼容）。
        registry = getattr(state, "citation_registry", None)
        if state.report_draft.citation_keys and registry is not None:
            invalid = [k for k in state.report_draft.citation_keys if not registry.contains(k)]
            if invalid:
                issues.append(
                    _issue(
                        "invalid_citation_key",
                        QualitySeverity.CRITICAL,
                        "writer",
                        f"存在非法 citation key: "
                        f"{', '.join(sorted(set(invalid))[:3])}"
                        "（不在注册表中，模型不得自行生成）",
                        QualityAction.REVISE_REPORT,
                    )
                )
        # P06-11F：上下文规则检测真实投资建议（免责声明不误判）。
        advice_hits = find_forbidden_advice(md)
        for phrase in advice_hits:
            issues.append(
                _issue(
                    "forbidden_advice",
                    QualitySeverity.CRITICAL,
                    "writer",
                    f"禁止的投资建议: {phrase}",
                    QualityAction.REJECT,
                )
            )

    return issues


def recommendation_from_issues(
    issues: list[QualityIssue], warnings: list[str]
) -> QualityRecommendation:
    """聚合最终建议：CRITICAL→REJECT；ERROR→REVISE；仅警告→PUBLISH_PARTIAL；无→PUBLISH。"""
    if any(i.severity == QualitySeverity.CRITICAL for i in issues):
        return QualityRecommendation.REJECT
    if any(i.action == QualityAction.REVISE_REPORT for i in issues):
        return QualityRecommendation.REVISE
    if warnings:
        return QualityRecommendation.PUBLISH_PARTIAL
    return QualityRecommendation.PUBLISH


def all_passed_from_issues(issues: list[QualityIssue]) -> bool:
    """可直接发布（无 ERROR/CRITICAL，PUBLISH_PARTIAL 视为可发布）。"""
    return not any(i.severity in (QualitySeverity.ERROR, QualitySeverity.CRITICAL) for i in issues)
