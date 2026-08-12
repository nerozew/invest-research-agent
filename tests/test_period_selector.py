"""P02-14 可比期间选择器契约测试（pure functions）。

验证目标（docs/05 P02-14 验收）：
- 52/53 周财年：以 period_end 所在年归一（不依赖 "52 周" 文本）；
- 季度 vs YTD：preference=ytd 时选最长期间（YTD 优先），quarterly 时选最短季度；
- 修订申报：同期间同时存在 10-K 与 10-K/A 时，修订版（10-K/A）优先；
- 同 concept 多期间：按 preference 排序选最优；
- as_of 截止日过滤：只返回 period_end <= as_of 的期间；
- 时点型（instant）fact 不进入期间选择；
- 不修改数据库、不联网；使用 FinancialFact（domain 模型）作为输入。

注意：FinancialFact.value 有 gt=0 约束，测试数值用正数即可。
FinancialFact 模型层已保证"期间型必须 start+end 同时存在"，选择器无需防御非法期间对象。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.domain.models import FinancialFact
from invest_research.financial.period_selector import (
    PeriodPreference,
    select_facts_for_period,
    select_report_period,
)


def _fact(
    *,
    concept: str = "RevenueFromContractWithCustomerExcludingAssessedTax",
    value: str = "100",
    period_start: date | None,
    period_end: date,
    form_type: str | None = "10-K",
) -> FinancialFact:
    return FinancialFact(
        company_id="msft",
        source_id="src",
        taxonomy="us-gaap",
        concept=concept,
        value=Decimal(value),
        unit="USD",
        period_start=period_start,
        period_end=period_end,
        form_type=form_type,
        fact_version="v1",
    )


def _instant_fact(
    *, concept: str = "Assets", value: str = "50", instant_date: date
) -> FinancialFact:
    return FinancialFact(
        company_id="msft",
        source_id="src",
        taxonomy="us-gaap",
        concept=concept,
        value=Decimal(value),
        unit="USD",
        instant_date=instant_date,
        fact_version="v1",
    )


# ---------------------------------------------------------------------------
# 季度 vs YTD（期间长度偏好）
# ---------------------------------------------------------------------------


def test_ytd_prefers_longest_duration() -> None:
    """preference=ytd：同一财年内选最长的期间（YTD 优先于单季度）。"""
    q1 = _fact(period_start=date(2025, 1, 1), period_end=date(2025, 3, 31), form_type="10-Q")
    ytd = _fact(period_start=date(2025, 1, 1), period_end=date(2025, 6, 30), form_type="10-Q")

    selected = select_facts_for_period(
        facts=[q1, ytd],
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        preference=PeriodPreference.YTD,
        as_of=date(2025, 7, 1),
    )

    assert len(selected) == 1
    assert selected[0] is ytd  # 更长的期间（YTD）胜出


def test_quarterly_prefers_shortest_duration() -> None:
    """preference=quarterly：选最短的期间（单季度优先）。"""
    q1 = _fact(
        period_start=date(2025, 1, 1), period_end=date(2025, 3, 31), form_type="10-Q"
    )  # 90 天
    q2 = _fact(
        period_start=date(2025, 4, 1), period_end=date(2025, 6, 30), form_type="10-Q"
    )  # 91 天
    ytd = _fact(
        period_start=date(2025, 1, 1), period_end=date(2025, 6, 30), form_type="10-Q"
    )  # 181 天

    selected = select_facts_for_period(
        facts=[q1, ytd, q2],
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        preference=PeriodPreference.QUARTERLY,
        as_of=date(2025, 7, 1),
    )

    assert len(selected) == 1
    assert selected[0] is q1  # 最短：90 天单季度


def test_annual_prefers_12_month_period() -> None:
    """preference=annual：选期间长度最接近 12 个月的（财年 10-K）。"""
    fy = _fact(period_start=date(2024, 7, 1), period_end=date(2025, 6, 30), form_type="10-K")
    ytd = _fact(period_start=date(2025, 1, 1), period_end=date(2025, 6, 30), form_type="10-Q")

    selected = select_facts_for_period(
        facts=[ytd, fy],
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        preference=PeriodPreference.ANNUAL,
        as_of=date(2025, 7, 1),
    )

    assert len(selected) == 1
    assert selected[0] is fy


def test_equal_length_prefers_latest_period_end() -> None:
    """期间长度相同时，选 period_end 最新者。"""
    early = _fact(period_start=date(2023, 7, 1), period_end=date(2024, 6, 30), form_type="10-K")
    latest = _fact(period_start=date(2024, 7, 1), period_end=date(2025, 6, 30), form_type="10-K")

    selected = select_facts_for_period(
        facts=[early, latest],
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        preference=PeriodPreference.ANNUAL,
        as_of=date(2025, 7, 1),
    )

    assert len(selected) == 1
    assert selected[0] is latest


# ---------------------------------------------------------------------------
# 财年归一（52/53 周）
# ---------------------------------------------------------------------------


def test_fiscal_52_53_week_normalized_by_period_end_year() -> None:
    """52/53 周财年：以 period_end 所在年归一（不依赖 "52 周" 文本标记）。"""
    fy2023 = _fact(period_start=date(2022, 7, 1), period_end=date(2023, 6, 30), form_type="10-K")
    fy2024 = _fact(
        period_start=date(2023, 7, 1),
        period_end=date(2024, 6, 28),
        form_type="10-K",  # 53 周财年
    )

    selected = select_facts_for_period(
        facts=[fy2023, fy2024],
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        preference=PeriodPreference.ANNUAL,
        as_of=date(2025, 1, 1),
    )

    assert len(selected) == 1
    assert selected[0] is fy2024  # period_end 2024-06-28 > 2023-06-30


# ---------------------------------------------------------------------------
# 修订申报优先
# ---------------------------------------------------------------------------


def test_amended_filing_wins_for_same_period() -> None:
    """同期间同时存在 10-K 与 10-K/A：修订版（10-K/A）优先（数据更权威）。"""
    original = _fact(period_start=date(2024, 7, 1), period_end=date(2025, 6, 30), form_type="10-K")
    amended = _fact(period_start=date(2024, 7, 1), period_end=date(2025, 6, 30), form_type="10-K/A")

    selected = select_facts_for_period(
        facts=[original, amended],
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        preference=PeriodPreference.ANNUAL,
        as_of=date(2025, 7, 1),
    )

    assert len(selected) == 1
    assert selected[0] is amended


def test_amendment_priority_only_for_same_period() -> None:
    """不同 period_end 时修订标记不影响选择（各自按 length 独立比较）。"""
    q1 = _fact(
        period_start=date(2025, 1, 1), period_end=date(2025, 3, 31), form_type="10-Q"
    )  # 90 天
    q2_a = _fact(
        period_start=date(2025, 4, 1), period_end=date(2025, 6, 30), form_type="10-Q/A"
    )  # 91 天

    selected = select_facts_for_period(
        facts=[q1, q2_a],
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        preference=PeriodPreference.QUARTERLY,
        as_of=date(2025, 7, 1),
    )

    assert len(selected) == 1
    assert selected[0] is q1  # quarterly 选最短（90 天），修订标记不跨期间生效


# ---------------------------------------------------------------------------
# as_of 截止日过滤
# ---------------------------------------------------------------------------


def test_as_of_filters_periods() -> None:
    """as_of 只保留 period_end <= as_of 的期间。"""
    fy2024 = _fact(period_start=date(2023, 7, 1), period_end=date(2024, 6, 30), form_type="10-K")
    fy2025 = _fact(period_start=date(2024, 7, 1), period_end=date(2025, 6, 30), form_type="10-K")

    selected = select_facts_for_period(
        facts=[fy2024, fy2025],
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        preference=PeriodPreference.ANNUAL,
        as_of=date(2024, 12, 31),  # fy2025 尚未结束
    )

    assert len(selected) == 1
    assert selected[0] is fy2024


# ---------------------------------------------------------------------------
# 时点型（instant）排除
# ---------------------------------------------------------------------------


def test_instant_fact_not_selected_as_duration() -> None:
    """时点型（instant）fact 不进入期间选择——concept 匹配但返回空。"""
    instant = _instant_fact(instant_date=date(2025, 6, 30))

    selected = select_facts_for_period(
        facts=[instant],
        concept="Assets",
        preference=PeriodPreference.ANNUAL,
        as_of=date(2026, 1, 1),
    )

    assert selected == []


# ---------------------------------------------------------------------------
# 模型层保证（防御性说明）
# ---------------------------------------------------------------------------


def test_selector_ignores_duration_without_start() -> None:
    """选择器忽略缺 period_start 的期间行（模型允许该状态，选择器防御）。"""
    bad = FinancialFact(
        company_id="msft",
        source_id="src",
        taxonomy="us-gaap",
        concept="Revenue",
        value=Decimal("10"),
        unit="USD",
        period_end=date(2025, 6, 30),  # 只有 end，缺 start（模型允许）
        fact_version="v1",
    )
    good = _fact(
        concept="Revenue",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 3, 31),
        form_type="10-Q",
    )

    selected = select_facts_for_period(
        facts=[bad, good],
        concept="Revenue",
        preference=PeriodPreference.QUARTERLY,
        as_of=date(2026, 1, 1),
    )

    assert len(selected) == 1
    assert selected[0] is good


# ---------------------------------------------------------------------------
# select_report_period（返回 period_end 的便捷入口）
# ---------------------------------------------------------------------------


def test_select_report_period_returns_end_date() -> None:
    """select_report_period 返回选中最优期间的 period_end。"""
    fy = _fact(period_start=date(2024, 7, 1), period_end=date(2025, 6, 30), form_type="10-K")
    q = _fact(period_start=date(2025, 1, 1), period_end=date(2025, 6, 30), form_type="10-Q")

    end = select_report_period(
        facts=[fy, q],
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        preference=PeriodPreference.ANNUAL,
        as_of=date(2025, 7, 1),
    )

    assert end == date(2025, 6, 30)


def test_select_report_period_none_when_no_match() -> None:
    """无匹配期间 → None。"""
    assert (
        select_report_period(
            facts=[],
            concept="Revenue",
            preference=PeriodPreference.ANNUAL,
            as_of=date(2025, 1, 1),
        )
        is None
    )
