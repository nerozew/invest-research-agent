"""P07-05：按 canonical annual filing 严格选择事实并计算年度指标。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from invest_research.domain.models import FinancialFact, MetricResult, MetricStatus
from invest_research.financial.concept_mapping import ConceptMapping
from invest_research.financial.metrics import (
    compute_gross_margin,
    compute_net_income_growth,
    compute_net_margin,
    compute_operating_margin,
    compute_revenue_growth,
)
from invest_research.financial.metrics_balance import (
    compute_asset_liability_ratio,
    compute_current_ratio,
    compute_free_cash_flow,
    compute_operating_cash_flow_ratio,
    compute_roa,
)

_ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})


@dataclass(frozen=True)
class AnnualComparisonCalculation:
    """纯计算结果；构建器负责持久化和状态映射。"""

    facts: tuple[FinancialFact, ...]
    metrics: tuple[MetricResult, ...]
    limitations: tuple[str, ...]
    comparator_accession_number: str | None


@dataclass(frozen=True)
class _Choice:
    fact: FinancialFact | None
    limitation: str | None = None


def _mapping_candidates(mapping: ConceptMapping, logical_name: str) -> tuple[str, ...]:
    for entry in mapping.entries:
        if entry.metric_name == logical_name:
            return entry.candidates
    return ()


def _is_annual_fact(fact: FinancialFact, fiscal_year: int, accession: str) -> bool:
    return (
        fact.fiscal_year == fiscal_year
        and (fact.fiscal_period or "").upper() == "FY"
        and (fact.form_type or "").upper() in _ANNUAL_FORMS
        and fact.accession_number == accession
    )


def _select_one(
    facts: list[FinancialFact],
    *,
    candidates: tuple[str, ...],
    fiscal_year: int,
    accession: str | None,
    report_date: date | None,
    instant: bool,
    logical_name: str,
) -> _Choice:
    if accession is None:
        return _Choice(None, f"{logical_name}: 缺少可审计的年度 accession")
    for concept in candidates:
        matches = [
            fact
            for fact in facts
            if fact.concept == concept and _is_annual_fact(fact, fiscal_year, accession)
        ]
        if instant:
            matches = [
                fact
                for fact in matches
                if fact.instant_date is not None
                and (report_date is None or fact.instant_date == report_date)
            ]
        else:
            matches = [
                fact
                for fact in matches
                if fact.period_start is not None
                and fact.period_end is not None
                and (report_date is None or fact.period_end == report_date)
                and 300 <= (fact.period_end - fact.period_start).days <= 400
            ]
        if not matches:
            continue
        distinct_values = {(fact.value, fact.unit) for fact in matches}
        if len(distinct_values) != 1:
            return _Choice(None, f"{logical_name}: canonical filing 内存在冲突事实，拒绝选数")
        return _Choice(matches[0])
    return _Choice(None, f"{logical_name}: 未找到匹配 canonical filing 的年度事实")


def _select_pair(
    facts: list[FinancialFact],
    *,
    mapping: ConceptMapping,
    logical_name: str,
    target_year: int,
    target_accession: str,
    target_report_date: date,
    comparator_year: int,
    comparator_accession: str | None,
    comparator_report_date: date | None,
    instant: bool,
) -> tuple[_Choice, _Choice]:
    candidates = _mapping_candidates(mapping, logical_name)
    # 第一遍：同 concept（保持既有优先级语义——同一 concept 必须两年都存在）。
    for concept in candidates:
        target = _select_one(
            facts,
            candidates=(concept,),
            fiscal_year=target_year,
            accession=target_accession,
            report_date=target_report_date,
            instant=instant,
            logical_name=logical_name,
        )
        comparator = _select_one(
            facts,
            candidates=(concept,),
            fiscal_year=comparator_year,
            accession=comparator_accession,
            report_date=comparator_report_date,
            instant=instant,
            logical_name=logical_name,
        )
        if target.fact is not None and comparator.fact is not None:
            return target, comparator
    # 第二遍：跨 concept 兜底——target/comparator 各自独立从候选集合中选，
    # 允许两年使用不同 concept（如 GOOGL 换用 revenue 概念）。仅当第一遍无同
    # concept 命中时触发，保证同口径优先、不退化旧行为。
    target = _select_one(
        facts,
        candidates=candidates,
        fiscal_year=target_year,
        accession=target_accession,
        report_date=target_report_date,
        instant=instant,
        logical_name=logical_name,
    )
    comparator = _select_one(
        facts,
        candidates=candidates,
        fiscal_year=comparator_year,
        accession=comparator_accession,
        report_date=comparator_report_date,
        instant=instant,
        logical_name=logical_name,
    )
    if target.fact is not None and comparator.fact is not None:
        return target, comparator
    return (
        _Choice(None, f"{logical_name}: 两个财年不存在同口径、canonical 的可比事实"),
        _Choice(None, f"{logical_name}: 两个财年不存在同口径、canonical 的可比事实"),
    )


def _infer_comparator_accession(facts: list[FinancialFact], fiscal_year: int) -> str | None:
    """无 comparator 10-K 时，仅接受唯一可识别的年度 facts accession。"""
    accessions = {
        fact.accession_number
        for fact in facts
        if fact.fiscal_year == fiscal_year
        and (fact.fiscal_period or "").upper() == "FY"
        and (fact.form_type or "").upper() in _ANNUAL_FORMS
        and fact.accession_number
    }
    return next(iter(accessions)) if len(accessions) == 1 else None


def _with_sources(metric: MetricResult, facts: list[FinancialFact | None]) -> MetricResult:
    sources = [
        {
            "concept": fact.concept,
            "accession_number": fact.accession_number,
            "period_start": fact.period_start.isoformat() if fact.period_start else None,
            "period_end": fact.period_end.isoformat() if fact.period_end else None,
            "instant_date": fact.instant_date.isoformat() if fact.instant_date else None,
            "unit": fact.unit,
        }
        for fact in facts
        if fact is not None
    ]
    return metric.model_copy(
        update={"inputs_json": {**metric.inputs_json, "source_facts": sources}}
    )


def _unique_facts(facts: list[FinancialFact | None]) -> tuple[FinancialFact, ...]:
    unique: list[FinancialFact] = []
    seen: set[tuple[object, ...]] = set()
    for fact in facts:
        if fact is None:
            continue
        key = (
            fact.concept,
            fact.value,
            fact.unit,
            fact.period_start,
            fact.period_end,
            fact.instant_date,
            fact.accession_number,
        )
        if key not in seen:
            seen.add(key)
            unique.append(fact)
    return tuple(unique)


def compute_annual_comparison(
    facts: list[FinancialFact],
    *,
    mapping: ConceptMapping,
    job_id: str,
    target_fiscal_year: int,
    target_accession: str,
    target_report_date: date,
    comparator_fiscal_year: int,
    comparator_accession: str | None,
    comparator_report_date: date | None,
) -> AnnualComparisonCalculation:
    """计算固定 10 项指标；任何不确定性均降级为 NOT_COMPUTABLE。"""
    effective_comparator_accession = comparator_accession or _infer_comparator_accession(
        facts, comparator_fiscal_year
    )
    selected: list[FinancialFact | None] = []
    limitations: list[str] = []

    def one(name: str, *, instant: bool = False, comparator: bool = False) -> _Choice:
        year = comparator_fiscal_year if comparator else target_fiscal_year
        accession = effective_comparator_accession if comparator else target_accession
        report_date = comparator_report_date if comparator else target_report_date
        choice = _select_one(
            facts,
            candidates=_mapping_candidates(mapping, name),
            fiscal_year=year,
            accession=accession,
            report_date=report_date,
            instant=instant,
            logical_name=name,
        )
        selected.append(choice.fact)
        if choice.limitation:
            limitations.append(choice.limitation)
        return choice

    revenue, prior_revenue = _select_pair(
        facts,
        mapping=mapping,
        logical_name="revenue",
        target_year=target_fiscal_year,
        target_accession=target_accession,
        target_report_date=target_report_date,
        comparator_year=comparator_fiscal_year,
        comparator_accession=effective_comparator_accession,
        comparator_report_date=comparator_report_date,
        instant=False,
    )
    net_income, prior_net_income = _select_pair(
        facts,
        mapping=mapping,
        logical_name="net_income",
        target_year=target_fiscal_year,
        target_accession=target_accession,
        target_report_date=target_report_date,
        comparator_year=comparator_fiscal_year,
        comparator_accession=effective_comparator_accession,
        comparator_report_date=comparator_report_date,
        instant=False,
    )
    for choice in (revenue, prior_revenue, net_income, prior_net_income):
        selected.append(choice.fact)
        if choice.limitation:
            limitations.append(choice.limitation)

    gross_profit = one("gross_profit")
    cost_of_goods_sold = one("cost_of_goods_sold")
    operating_income = one("operating_income")
    current_assets = one("current_assets", instant=True)
    current_liabilities = one("current_liabilities", instant=True)
    total_assets = one("total_assets", instant=True)
    prior_total_assets = one("total_assets", instant=True, comparator=True)
    total_liabilities = one("total_liabilities", instant=True)
    liabilities_and_equity = one("liabilities_and_equity", instant=True)
    stockholders_equity = one("stockholders_equity", instant=True)
    operating_cash_flow = one("operating_cash_flow")
    capital_expenditure = one("capital_expenditure")

    period_end = target_report_date
    # 毛利/负债确定性推导：候选 concept 缺失时用勾稽关系（数字仍是 SEC 事实加减，不经 LLM）。
    gross_profit_value = gross_profit.fact.value if gross_profit.fact else None
    gross_sources: list[FinancialFact | None] = [revenue.fact, gross_profit.fact]
    if (
        gross_profit_value is None
        and revenue.fact is not None
        and cost_of_goods_sold.fact is not None
    ):
        gross_profit_value = revenue.fact.value - cost_of_goods_sold.fact.value
        gross_sources = [revenue.fact, cost_of_goods_sold.fact]
        limitations.append("gross_profit: 由 收入−销售成本 确定性推导")
        # 推导已解决，移除"未找到"的原始 limitation（避免报告同时显示缺失与推导）。
        limitations[:] = [
            lim for lim in limitations if not lim.startswith("gross_profit: 未找到")
        ]

    total_liabilities_value = total_liabilities.fact.value if total_liabilities.fact else None
    liab_sources: list[FinancialFact | None] = [total_liabilities.fact, total_assets.fact]
    if (
        total_liabilities_value is None
        and liabilities_and_equity.fact is not None
        and stockholders_equity.fact is not None
    ):
        total_liabilities_value = liabilities_and_equity.fact.value - stockholders_equity.fact.value
        liab_sources = [liabilities_and_equity.fact, stockholders_equity.fact]
        limitations.append("total_liabilities: 由 负债和权益−股东权益 确定性推导")
        limitations[:] = [
            lim for lim in limitations if not lim.startswith("total_liabilities: 未找到")
        ]
    metrics = (
        _with_sources(
            compute_revenue_growth(
                revenue.fact.value if revenue.fact else None,
                prior_revenue.fact.value if prior_revenue.fact else None,
                job_id=job_id,
                period_end=period_end,
            ),
            [revenue.fact, prior_revenue.fact],
        ),
        _with_sources(
            compute_gross_margin(
                revenue.fact.value if revenue.fact else None,
                gross_profit_value,
                job_id=job_id,
                period_end=period_end,
            ),
            gross_sources,
        ),
        _with_sources(
            compute_operating_margin(
                revenue.fact.value if revenue.fact else None,
                operating_income.fact.value if operating_income.fact else None,
                job_id=job_id,
                period_end=period_end,
            ),
            [revenue.fact, operating_income.fact],
        ),
        _with_sources(
            compute_net_margin(
                revenue.fact.value if revenue.fact else None,
                net_income.fact.value if net_income.fact else None,
                job_id=job_id,
                period_end=period_end,
            ),
            [revenue.fact, net_income.fact],
        ),
        _with_sources(
            compute_net_income_growth(
                net_income.fact.value if net_income.fact else None,
                prior_net_income.fact.value if prior_net_income.fact else None,
                job_id=job_id,
                period_end=period_end,
            ),
            [net_income.fact, prior_net_income.fact],
        ),
        _with_sources(
            compute_current_ratio(
                current_assets.fact.value if current_assets.fact else None,
                current_liabilities.fact.value if current_liabilities.fact else None,
                unit_assets=current_assets.fact.unit if current_assets.fact else None,
                unit_liabilities=current_liabilities.fact.unit
                if current_liabilities.fact
                else None,
                job_id=job_id,
                period_end=period_end,
            ),
            [current_assets.fact, current_liabilities.fact],
        ),
        _with_sources(
            compute_asset_liability_ratio(
                total_liabilities_value,
                total_assets.fact.value if total_assets.fact else None,
                unit_liabilities=liab_sources[0].unit if liab_sources[0] else None,
                unit_assets=total_assets.fact.unit if total_assets.fact else None,
                job_id=job_id,
                period_end=period_end,
            ),
            liab_sources,
        ),
        _with_sources(
            compute_operating_cash_flow_ratio(
                operating_cash_flow.fact.value if operating_cash_flow.fact else None,
                revenue.fact.value if revenue.fact else None,
                unit_cash_flow=operating_cash_flow.fact.unit if operating_cash_flow.fact else None,
                unit_revenue=revenue.fact.unit if revenue.fact else None,
                job_id=job_id,
                period_end=period_end,
            ),
            [operating_cash_flow.fact, revenue.fact],
        ),
        _with_sources(
            compute_free_cash_flow(
                operating_cash_flow.fact.value if operating_cash_flow.fact else None,
                capital_expenditure.fact.value if capital_expenditure.fact else None,
                unit_cash_flow=operating_cash_flow.fact.unit if operating_cash_flow.fact else None,
                unit_capex=capital_expenditure.fact.unit if capital_expenditure.fact else None,
                job_id=job_id,
                period_end=period_end,
            ),
            [operating_cash_flow.fact, capital_expenditure.fact],
        ),
        _with_sources(
            compute_roa(
                net_income.fact.value if net_income.fact else None,
                prior_total_assets.fact.value if prior_total_assets.fact else None,
                total_assets.fact.value if total_assets.fact else None,
                unit_income=net_income.fact.unit if net_income.fact else None,
                unit_assets=total_assets.fact.unit if total_assets.fact else None,
                job_id=job_id,
                period_end=period_end,
            ),
            [net_income.fact, prior_total_assets.fact, total_assets.fact],
        ),
    )
    limitations.extend(
        f"{metric.metric_name}: {metric.explanation}"
        for metric in metrics
        if metric.status is not MetricStatus.COMPUTED and metric.explanation
    )
    if comparator_accession is None and effective_comparator_accession is None:
        limitations.append("上一年度 10-K 缺失且 Company Facts 无唯一年度 accession，禁止同比计算")
    elif comparator_accession is None:
        limitations.append("上一年度 10-K 缺失；财务同比仅基于唯一可审计的 Company Facts accession")
    return AnnualComparisonCalculation(
        facts=_unique_facts(selected),
        metrics=metrics,
        limitations=tuple(dict.fromkeys(limitations)),
        comparator_accession_number=effective_comparator_accession,
    )
