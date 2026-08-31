"""P02-14 可比期间选择器（pure functions）。

在同一个 concept 的多条期间型 FinancialFact 中，按偏好挑选最“可比”的单条期间：
- ANNUAL  ：优先 period_end 最新（最新财年）；期间长度其次接近 365 天（容 52/53 周）；
- QUARTERLY：期间最短（单季度）优先，同长度取 period_end 最新；
- YTD    ：期间最长（年初至今）优先，同长度取 period_end 最新；
- 同一期间（start+end 相同）出现多条时，修订申报（form_type 以 /A 结尾）优先；
- period_end > as_of 的期间被过滤（截至日），防止使用未来/未结束期间；
- 时点型（instant）fact 不参与（期间选择只针对 duration）。

依赖边界：本层只依赖标准库与 domain 层（FinancialFact）；
禁止导入 CrewAI/FastAPI/SQLAlchemy/httpx。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from invest_research.domain.models import FinancialFact


class PeriodPreference(StrEnum):
    """期间偏好：年度/季度/年初至今。"""

    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    YTD = "ytd"


def _period_bounds(fact: FinancialFact) -> tuple[date, date]:
    """返回 (period_start, period_end)；期间型由调用方已保证非空，防御性断言。"""
    start, end = fact.period_start, fact.period_end
    assert start is not None and end is not None
    return start, end


def _is_amended(form_type: str | None) -> bool:
    """修订申报：10-K/A、10-Q/A（form_type 以 /A 结尾）。"""
    return bool(form_type and form_type.endswith("/A"))


def _prefer_amended(group: list[FinancialFact]) -> FinancialFact:
    """同期间（start+end 相同）内，修订版优先；否则保序取第一条。"""
    amended = [f for f in group if _is_amended(f.form_type)]
    return amended[0] if amended else group[0]


def _best_period(facts: list[FinancialFact], preference: PeriodPreference) -> FinancialFact:
    """按偏好从代表期间中选出最优一条。"""
    scored: list[tuple[tuple[int, int], FinancialFact]] = []
    for f in facts:
        start, end = _period_bounds(f)
        days = (end - start).days
        if preference == PeriodPreference.ANNUAL:
            # 最新 period_end 优先；同样新时长度越接近 365 天越好
            score = (-end.toordinal(), abs(days - 365))
        elif preference == PeriodPreference.QUARTERLY:
            # 最短优先；同样短时取更新
            score = (days, -end.toordinal())
        else:  # YTD
            # 最长优先；同样长时取更新
            score = (days, end.toordinal())
        scored.append((score, f))

    if preference == PeriodPreference.YTD:
        return max(scored, key=lambda t: t[0])[1]
    return min(scored, key=lambda t: t[0])[1]


def select_facts_for_period(
    facts: list[FinancialFact],
    concept: str,
    preference: PeriodPreference,
    as_of: date,
) -> list[FinancialFact]:
    """从 facts 中按 concept 与偏好选出单条最优期间（恰含一条；无匹配返回空）。

    - 只考虑期间型（period_start/period_end 均非空）；
    - period_end > as_of 被过滤；
    - 同期间多条时修订版优先，再按 preference 排序选最优。
    """
    duration_facts: list[FinancialFact] = []
    for f in facts:
        if (
            f.concept == concept
            and f.period_start is not None
            and f.period_end is not None
            and f.period_end <= as_of
        ):
            duration_facts.append(f)
    if not duration_facts:
        return []

    # 按 (start, end) 分组，组内选修订版（同期间去重）
    groups: dict[tuple[date, date], list[FinancialFact]] = {}
    for f in duration_facts:
        start, end = _period_bounds(f)
        groups.setdefault((start, end), []).append(f)
    representatives = [_prefer_amended(g) for g in groups.values()]

    best = _best_period(representatives, preference)
    return [best]


def select_report_period(
    facts: list[FinancialFact],
    concept: str,
    preference: PeriodPreference,
    as_of: date,
) -> date | None:
    """便捷入口：返回最优期间的 period_end；无匹配返回 None。"""
    selected = select_facts_for_period(facts, concept, preference, as_of)
    if not selected:
        return None
    return selected[0].period_end
