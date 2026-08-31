"""P06-11F：统一空值与不完整结果策略（EmptyValuePolicy）。

设计动机：
- 不要"任何字段为空都拒绝"，也不要"任何字段为空都通过"；
- 按字段语义把可能为空的字段分成 REQUIRED（必须存在）/
  CONDITIONALLY_OPTIONAL（条件可空，配合 complete/partial/unavailable 状态）/
  OPTIONAL（普通可选），集中在一个纯函数模块判断；
- 空字符串、空数组、None 必须按字段语义区分，不能一概视为相同；
- 对声明为 optional 的文本字段做保守规范化：纯空白字符串 → None；
  禁止为必填字段补造内容。

核心 API：
- ``EmptyPolicy`` 枚举：REQUIRED / CONDITIONALLY_OPTIONAL / OPTIONAL；
- ``resolve_empty_policy(field_descriptor)``：按字段名返回策略；
- ``is_missing(value, policy)``：按策略判断是否"缺失"；
- ``normalize_optional_text(value, policy)``：仅对 OPTIONAL 文本字段
  把纯空白字符串规范化为 None；
- ``check_analysis_state(completeness, facts, metrics, limitations,
  unavailable_reason)``：实现四级完整性判定，返回
  ``CompletenessVerdict``（reject / revise / publish_partial / pass）。

依赖方向：application → 标准库。纯函数，不导入 CrewAI/FastAPI/Redis。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EmptyPolicy(StrEnum):
    """空值策略分类（按字段语义，禁止一律视为相同）。"""

    REQUIRED = "required"
    CONDITIONALLY_OPTIONAL = "conditionally_optional"
    OPTIONAL = "optional"


class CompletenessVerdict(StrEnum):
    """完整性判定结果（供 Quality Gate 与报告展示使用）。"""

    REJECT = "reject"
    REVISE = "revise"
    PUBLISH_PARTIAL = "publish_partial"
    PASS = "pass"


# 各字段名 → 空值策略（集中管理，不把空值判断散落在多个 if 中）。
# REQUIRED：缺失代表任务无法成立或报告无法追溯（pack/正文/公司身份/引用）。
# CONDITIONALLY_OPTIONAL：数据确实无法取得时允许为空，但必须配合完整状态说明。
# OPTIONAL：空值不影响报告真实性，只产生 warning。
_FIELD_POLICY: dict[str, EmptyPolicy] = {
    # --- REQUIRED（缺失即任务无法成立或报告无法追溯）---
    "research_pack": EmptyPolicy.REQUIRED,
    "analysis_pack": EmptyPolicy.REQUIRED,
    "report_markdown": EmptyPolicy.REQUIRED,
    "report_title": EmptyPolicy.REQUIRED,
    "company_identity": EmptyPolicy.REQUIRED,
    "as_of_date": EmptyPolicy.REQUIRED,
    "period_end": EmptyPolicy.REQUIRED,
    "citation_keys": EmptyPolicy.REQUIRED,  # 报告引用外部事实时必须存在合法 key
    # --- CONDITIONALLY_OPTIONAL（配合 complete/partial/unavailable 状态）---
    "facts": EmptyPolicy.CONDITIONALLY_OPTIONAL,
    "metrics": EmptyPolicy.CONDITIONALLY_OPTIONAL,
    "limitations": EmptyPolicy.CONDITIONALLY_OPTIONAL,
    "unavailable_reason": EmptyPolicy.CONDITIONALLY_OPTIONAL,
    "coverage_notes": EmptyPolicy.CONDITIONALLY_OPTIONAL,
    # --- OPTIONAL（空值不影响真实性）---
    "analysis_notes": EmptyPolicy.OPTIONAL,
    "conflicts": EmptyPolicy.OPTIONAL,
    "source_title": EmptyPolicy.OPTIONAL,
    "source_publisher": EmptyPolicy.OPTIONAL,
    "source_locator": EmptyPolicy.OPTIONAL,
}


def resolve_empty_policy(field: str) -> EmptyPolicy:
    """按字段名返回空值策略（未知字段默认 REQUIRED——宁可错杀不可放行）。"""
    return _FIELD_POLICY.get(field, EmptyPolicy.REQUIRED)


def _is_blank_text(value: Any) -> bool:
    """判断值是否为纯空白字符串（'' / '   ' / '\\n' → True）。"""
    return isinstance(value, str) and not value.strip()


def is_missing(value: Any, policy: EmptyPolicy) -> bool:
    """按策略判断值是否缺失。

    - REQUIRED：None、空字符串、空列表/元组/字典都算缺失；
    - CONDITIONALLY_OPTIONAL：None、空列表/元组/字典都算缺失
      （是否允许由调用方的状态校验决定——partial 需要 limitations、
      unavailable 需要 unavailable_reason）；
    - OPTIONAL：仅 None 算缺失（空字符串是"空值"但不阻断发布）。
    """
    if value is None:
        return True
    if policy == EmptyPolicy.OPTIONAL:
        # 可选字段：空字符串/空数组不阻断，但视为"空"。这里只把 None 视为缺失。
        return False
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    if isinstance(value, str):
        return _is_blank_text(value)
    return False


def normalize_optional_text(value: Any, policy: EmptyPolicy) -> Any:
    """保守规范化：仅对 OPTIONAL 文本字段把纯空白字符串 → None。

    - 对 REQUIRED / CONDITIONALLY_OPTIONAL 字段**不做任何规范化**
      （禁止为必填字段补造内容，也禁止把合法空字符串静默改成 None；
      那些字段的缺失由 is_missing 与状态校验裁决）。
    - 非文本值原样返回。
    """
    if policy == EmptyPolicy.OPTIONAL and isinstance(value, str):
        return value.strip() or None
    return value


@dataclass(frozen=True)
class AnalysisCompletenessCheck:
    """财务完整性判定的结构化结果（供 Quality Report 记录）。

    - ``verdict``：最终建议（reject/revise/publish_partial/pass）；
    - ``issues``：需要写入 QualityReport 的问题说明（含空字段、原因与影响）；
    - ``data_integrity``：数据完整性标签（完整/部分/不可用）。
    """

    verdict: CompletenessVerdict
    issues: list[str]
    data_integrity: str = "complete"


def check_analysis_completeness(
    *,
    completeness: str,
    facts: list[Any],
    metrics: list[Any],
    limitations: list[str],
    unavailable_reason: str | None,
) -> AnalysisCompletenessCheck:
    """统一财务完整性判定（纯函数，替代散落 if）。

    规则：
    - complete：必须存在 facts 或 metrics（核心事实）；缺失 → REJECT
      （不允许静默降级为 partial）；
    - partial：必须存在 limitations（说明缺哪些数据及原因）；
      缺 limitations → REJECT；有 limitations → 允许 PUBLISH_PARTIAL；
    - unavailable：必须存在 unavailable_reason；缺 → REJECT；
      有 reason → 允许 PUBLISH_PARTIAL（报告只写"数据不可用"）。
    - 其它未知状态 → REJECT。
    """
    state = (completeness or "").strip().lower()
    if state == "complete":
        if not facts and not metrics:
            return AnalysisCompletenessCheck(
                verdict=CompletenessVerdict.REJECT,
                issues=[
                    "completeness=complete 但 facts/metrics 为空（核心事实缺失，不允许静默放行）"
                ],
                data_integrity="complete",
            )
        return AnalysisCompletenessCheck(
            verdict=CompletenessVerdict.PASS,
            issues=[],
            data_integrity="complete",
        )
    if state == "partial":
        if not limitations:
            return AnalysisCompletenessCheck(
                verdict=CompletenessVerdict.REJECT,
                issues=["completeness=partial 但 limitations 为空（必须说明缺哪些数据及原因）"],
                data_integrity="partial",
            )
        return AnalysisCompletenessCheck(
            verdict=CompletenessVerdict.PUBLISH_PARTIAL,
            issues=[
                "completeness=partial：报告应披露 limitations 中的缺失数据"
                "（合法 partial 不应被当成系统异常）"
            ],
            data_integrity="partial",
        )
    if state == "unavailable":
        if not unavailable_reason or not unavailable_reason.strip():
            return AnalysisCompletenessCheck(
                verdict=CompletenessVerdict.REJECT,
                issues=["completeness=unavailable 但 unavailable_reason 为空"],
                data_integrity="unavailable",
            )
        return AnalysisCompletenessCheck(
            verdict=CompletenessVerdict.PUBLISH_PARTIAL,
            issues=[
                "completeness=unavailable：报告只写数据不可用"
                "（引用 unavailable_reason），不得伪造财务数据"
            ],
            data_integrity="unavailable",
        )
    return AnalysisCompletenessCheck(
        verdict=CompletenessVerdict.REJECT,
        issues=[f"未知 completeness 状态: {completeness}"],
        data_integrity="unknown",
    )
