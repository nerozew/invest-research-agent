"""P07 三张财务报表确定性导出测试。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.domain.models import FinancialFact
from invest_research.financial.annual_statements import (
    FinancialStatementKind,
    extract_statements,
    load_statement_mapping,
)

TARGET_ACC = "0000000000-26-000001"
COMPARATOR_ACC = "0000000000-25-000001"


def _fact(
    concept: str, value: str, *, year: int, accession: str, instant: bool = False
) -> FinancialFact:
    return FinancialFact(
        company_id="0000789019",
        source_id="sec-facts",
        taxonomy="us-gaap",
        concept=concept,
        value=Decimal(value),
        unit="USD",
        period_start=None if instant else date(year - 1, 7, 1),
        period_end=None if instant else date(year, 6, 30),
        instant_date=date(year, 6, 30) if instant else None,
        fiscal_year=year,
        fiscal_period="FY",
        form_type="10-K",
        accession_number=accession,
    )


def _facts() -> list[FinancialFact]:
    facts: list[FinancialFact] = []
    for concept, value in {
        "Assets": "1000",
        "Liabilities": "600",
        "StockholdersEquity": "400",
        "AssetsCurrent": "300",
        "Inventory": "50",
        "LiabilitiesCurrent": "200",
    }.items():
        facts.append(_fact(concept, value, year=2026, accession=TARGET_ACC, instant=True))
        facts.append(
            _fact(
                concept,
                str(Decimal(value) - 100),
                year=2025,
                accession=COMPARATOR_ACC,
                instant=True,
            )
        )
    for concept, value in {
        "Revenues": "500",
        "CostOfGoodsAndServicesSold": "300",
        "GrossProfit": "200",
        "NetIncomeLoss": "80",
    }.items():
        facts.append(_fact(concept, value, year=2026, accession=TARGET_ACC))
        facts.append(_fact(concept, str(Decimal(value) - 50), year=2025, accession=COMPARATOR_ACC))
    for concept, value in {
        "NetCashProvidedByUsedInOperatingActivities": "150",
        "PaymentsToAcquirePropertyPlantAndEquipment": "-40",
    }.items():
        facts.append(_fact(concept, value, year=2026, accession=TARGET_ACC))
        facts.append(_fact(concept, str(Decimal(value) - 20), year=2025, accession=COMPARATOR_ACC))
    return facts


def test_extract_statements_screens_by_year_and_accession() -> None:
    mapping = load_statement_mapping()
    sets = extract_statements(
        _facts(),
        mapping,
        target_year=2026,
        target_accession=TARGET_ACC,
        comparator_year=2025,
        comparator_accession=COMPARATOR_ACC,
    )
    assert {s.kind for s in sets} == {
        FinancialStatementKind.BALANCE_SHEET,
        FinancialStatementKind.INCOME_STATEMENT,
        FinancialStatementKind.CASH_FLOW,
    }
    balance = next(s for s in sets if s.kind is FinancialStatementKind.BALANCE_SHEET)
    by_label = {row.label: row for row in balance.rows}
    assert by_label["资产总计"].value == Decimal("1000")
    assert by_label["资产总计"].comparator_value == Decimal("900")
    assert by_label["资产总计"].period_type.value == "instant"
    income = next(s for s in sets if s.kind is FinancialStatementKind.INCOME_STATEMENT)
    assert {row.label for row in income.rows} >= {"收入", "毛利润", "净利润"}
    income_by_label = {row.label: row for row in income.rows}
    assert income_by_label["收入"].value == Decimal("500")
    assert income_by_label["收入"].period_type.value == "duration"
    # 勾稽校验：资产 1000 == 负债 600 + 权益 400。
    assert balance.balance_check is not None
    assert balance.balance_check.ok is True


def test_extract_statements_missing_row_degrades_with_limitation() -> None:
    facts = [
        fact
        for fact in _facts()
        if not (fact.concept == "Inventory" and fact.accession_number == TARGET_ACC)
    ]
    mapping = load_statement_mapping()
    sets = extract_statements(
        facts,
        mapping,
        target_year=2026,
        target_accession=TARGET_ACC,
        comparator_year=2025,
        comparator_accession=COMPARATOR_ACC,
    )
    balance = next(s for s in sets if s.kind is FinancialStatementKind.BALANCE_SHEET)
    inventory = next(row for row in balance.rows if row.label == "存货")
    # target 年 Inventory 被移除 → 本期值缺失（缺行降级），comparator 年仍可读。
    assert inventory.value is None
    assert any("存货" in limitation for limitation in balance.limitations)


def test_extract_statements_conflicting_values_rejected() -> None:
    facts = [*_facts(), _fact("Assets", "9999", year=2026, accession=TARGET_ACC, instant=True)]
    mapping = load_statement_mapping()
    sets = extract_statements(
        facts, mapping, target_year=2026, target_accession=TARGET_ACC
    )
    balance = next(s for s in sets if s.kind is FinancialStatementKind.BALANCE_SHEET)
    assets = next(row for row in balance.rows if row.label == "资产总计")
    assert assets.value is None
    assert any("资产总计" in limitation for limitation in balance.limitations)


def test_extract_statements_requires_exact_report_date_for_conflicting_periods() -> None:
    """同 concept 同财年同 accession 多个 FY 期间（值不同）→ 必须按 report_date 精确匹配。

    真实回归：NVDA 的 Company Facts 对同一概念在 canonical filing 内有多个 FY 期间，
    不传 report_date 会全部冲突拒绝（真实 canary 暴露的 bug）。
    """
    mapping = load_statement_mapping()
    facts = [
        _fact("Assets", "1000", year=2026, accession=TARGET_ACC, instant=True),
        FinancialFact(
            company_id="0000789019",
            source_id="sec-facts",
            taxonomy="us-gaap",
            concept="Assets",
            value=Decimal("1100"),
            unit="USD",
            instant_date=date(2026, 1, 30),  # 同财年另一 FY 期间，值不同
            fiscal_year=2026,
            fiscal_period="FY",
            form_type="10-K",
            accession_number=TARGET_ACC,
        ),
    ]
    # 不传 report_date → 冲突拒绝。
    sets = extract_statements(facts, mapping, target_year=2026, target_accession=TARGET_ACC)
    balance = next(s for s in sets if s.kind is FinancialStatementKind.BALANCE_SHEET)
    assets = next(row for row in balance.rows if row.label == "资产总计")
    assert assets.value is None
    # 传 report_date 精确匹配 → 选到唯一事实。
    sets = extract_statements(
        facts,
        mapping,
        target_year=2026,
        target_accession=TARGET_ACC,
        target_report_date=date(2026, 6, 30),
    )
    balance = next(s for s in sets if s.kind is FinancialStatementKind.BALANCE_SHEET)
    assets = next(row for row in balance.rows if row.label == "资产总计")
    assert assets.value == Decimal("1000")


def _facts_for_derivation() -> list[FinancialFact]:
    """收入/成本/LSE/权益有值；毛利与负债合计**无候选命中**（触发确定性推导）。"""
    facts: list[FinancialFact] = []
    for concept, value in {
        "Revenues": "500",
        "CostOfGoodsAndServicesSold": "300",
    }.items():
        facts.append(_fact(concept, value, year=2026, accession=TARGET_ACC))
        facts.append(_fact(concept, str(Decimal(value) - 50), year=2025, accession=COMPARATOR_ACC))
    for concept, value in {
        "LiabilitiesAndStockholdersEquity": "1000",
        "StockholdersEquity": "400",
    }.items():
        facts.append(_fact(concept, value, year=2026, accession=TARGET_ACC, instant=True))
        facts.append(
            _fact(
                concept,
                str(Decimal(value) - 100),
                year=2025,
                accession=COMPARATOR_ACC,
                instant=True,
            )
        )
    return facts


def test_derives_gross_profit_from_revenue_minus_cost() -> None:
    """毛利候选 concept 缺失时，用同表 收入−销售成本 确定性推导。"""
    sets = extract_statements(
        _facts_for_derivation(),
        load_statement_mapping(),
        target_year=2026,
        target_accession=TARGET_ACC,
        comparator_year=2025,
        comparator_accession=COMPARATOR_ACC,
    )
    income = next(s for s in sets if s.kind is FinancialStatementKind.INCOME_STATEMENT)
    gross = next(r for r in income.rows if r.label == "毛利润")
    assert gross.value == Decimal("200")  # 500 - 300
    assert gross.comparator_value == Decimal("200")  # 450 - 250
    assert gross.derivation_source == ("收入", "销售成本")


def test_derives_total_liabilities_from_lse_minus_equity() -> None:
    """负债合计候选 concept 缺失时，用 负债和权益合计−股东权益 推导。"""
    sets = extract_statements(
        _facts_for_derivation(),
        load_statement_mapping(),
        target_year=2026,
        target_accession=TARGET_ACC,
        comparator_year=2025,
        comparator_accession=COMPARATOR_ACC,
    )
    balance = next(s for s in sets if s.kind is FinancialStatementKind.BALANCE_SHEET)
    liabilities = next(r for r in balance.rows if r.label == "负债合计")
    assert liabilities.value == Decimal("600")  # 1000 - 400
    assert liabilities.derivation_source == ("负债和权益合计", "股东权益")


def test_derivation_missing_input_marks_no_derivation() -> None:
    """推导输入缺失（无销售成本）→ value=None + NO_DERIVATION limitation。"""
    facts = [f for f in _facts_for_derivation() if f.concept != "CostOfGoodsAndServicesSold"]
    sets = extract_statements(
        facts,
        load_statement_mapping(),
        target_year=2026,
        target_accession=TARGET_ACC,
    )
    income = next(s for s in sets if s.kind is FinancialStatementKind.INCOME_STATEMENT)
    gross = next(r for r in income.rows if r.label == "毛利润")
    assert gross.value is None
    assert any("NO_DERIVATION" in lim for lim in income.limitations)


def test_concept_missing_without_derivation_keeps_limitation() -> None:
    """无候选也无推导的行仍保持原 limitation（CONCEPT_MISSING 文案）。"""
    sets = extract_statements(
        _facts_for_derivation(),
        load_statement_mapping(),
        target_year=2026,
        target_accession=TARGET_ACC,
    )
    balance = next(s for s in sets if s.kind is FinancialStatementKind.BALANCE_SHEET)
    cash = next(r for r in balance.rows if r.label == "现金及现金等价物")
    assert cash.value is None
    assert any("未找到匹配 canonical filing" in lim for lim in balance.limitations)
