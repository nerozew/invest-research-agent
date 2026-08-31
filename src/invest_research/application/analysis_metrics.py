"""从可信 SEC FinancialFact 确定性计算核心财务指标。

LLM 只负责选择和解释事实；数值、期间匹配和公式计算全部由本服务完成。
这样即使 Agent 未调用 FinancialCalculator，最终 AnalysisPack 仍能得到可追溯的
Decimal 指标，缺少输入时则明确返回 NOT_COMPUTABLE，绝不猜测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from invest_research.domain.models import FinancialFact, MetricResult, MetricStatus
from invest_research.financial.concept_mapping import (
    CONCEPTS_V1_PATH,
    ConceptMapping,
    load_concept_mapping,
    select_concept,
)
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


@dataclass(frozen=True)
class DeterministicMetricBundle:
    """一次确定性计算的结果和诚实限制。"""

    metrics: tuple[MetricResult, ...]
    limitations: tuple[str, ...]


@lru_cache(maxsize=1)
def _mapping() -> ConceptMapping:
    return load_concept_mapping(CONCEPTS_V1_PATH)


def _effective_date(fact: FinancialFact) -> date | None:
    return fact.period_end or fact.instant_date


def _is_annual(fact: FinancialFact) -> bool:
    form = (fact.form_type or "").upper()
    if form in {"10-K", "10-K/A"} or (fact.fiscal_period or "").upper() == "FY":
        return True
    if fact.period_start is not None and fact.period_end is not None:
        return 300 <= (fact.period_end - fact.period_start).days <= 400
    return False


def _logical_facts(
    facts: list[FinancialFact], logical_name: str, *, as_of: date
) -> list[FinancialFact]:
    available = tuple({fact.concept for fact in facts})
    concept = select_concept(_mapping(), logical_name, available)
    if concept is None:
        return []
    selected = [
        fact
        for fact in facts
        if fact.concept == concept
        and (effective := _effective_date(fact)) is not None
        and effective <= as_of
    ]
    selected.sort(
        key=lambda fact: (
            _effective_date(fact) or date.min,
            (fact.form_type or "").endswith("/A"),
        ),
        reverse=True,
    )
    return selected


def _annual_duration(
    facts: list[FinancialFact], logical_name: str, *, as_of: date
) -> list[FinancialFact]:
    candidates = [
        fact
        for fact in _logical_facts(facts, logical_name, as_of=as_of)
        if fact.period_end is not None and _is_annual(fact)
    ]
    seen: set[date] = set()
    result: list[FinancialFact] = []
    for fact in candidates:
        assert fact.period_end is not None
        if fact.period_end in seen:
            continue
        seen.add(fact.period_end)
        result.append(fact)
    return result


def _duration_at(
    facts: list[FinancialFact], logical_name: str, target: date
) -> FinancialFact | None:
    return next(
        (
            fact
            for fact in _annual_duration(facts, logical_name, as_of=target)
            if fact.period_end == target
        ),
        None,
    )


def _instant_at_or_before(
    facts: list[FinancialFact], logical_name: str, target: date, *, offset: int = 0
) -> FinancialFact | None:
    candidates = [
        fact
        for fact in _logical_facts(facts, logical_name, as_of=target)
        if fact.instant_date is not None
    ]
    seen: set[date] = set()
    unique: list[FinancialFact] = []
    for fact in candidates:
        assert fact.instant_date is not None
        if fact.instant_date in seen:
            continue
        seen.add(fact.instant_date)
        unique.append(fact)
    return unique[offset] if len(unique) > offset else None


def _value(fact: FinancialFact | None):  # type: ignore[no-untyped-def]
    return fact.value if fact is not None else None


def _with_sources(result: MetricResult, facts: list[FinancialFact | None]) -> MetricResult:
    sources = []
    for fact in facts:
        if fact is None:
            continue
        sources.append(
            {
                "source_id": fact.source_id,
                "concept": fact.concept,
                "period_start": fact.period_start.isoformat() if fact.period_start else None,
                "period_end": fact.period_end.isoformat() if fact.period_end else None,
                "instant_date": fact.instant_date.isoformat() if fact.instant_date else None,
                "accession_number": fact.accession_number,
                "unit": fact.unit,
            }
        )
    return result.model_copy(
        update={"inputs_json": {**result.inputs_json, "source_facts": sources}}
    )


def compute_deterministic_metrics(
    facts: list[FinancialFact], *, job_id: str, as_of: date
) -> DeterministicMetricBundle:
    """从 SEC 事实生成固定的 10 项指标；缺输入时返回可追溯限制。"""
    revenue_periods = _annual_duration(facts, "revenue", as_of=as_of)
    target = revenue_periods[0].period_end if revenue_periods else as_of
    assert target is not None

    revenue = _duration_at(facts, "revenue", target)
    prior_revenue = revenue_periods[1] if len(revenue_periods) > 1 else None
    gross_profit = _duration_at(facts, "gross_profit", target)
    operating_income = _duration_at(facts, "operating_income", target)
    net_income = _duration_at(facts, "net_income", target)
    net_income_periods = _annual_duration(facts, "net_income", as_of=target)
    prior_net_income = net_income_periods[1] if len(net_income_periods) > 1 else None
    current_assets = _instant_at_or_before(facts, "current_assets", target)
    current_liabilities = _instant_at_or_before(facts, "current_liabilities", target)
    total_assets = _instant_at_or_before(facts, "total_assets", target)
    prior_total_assets = _instant_at_or_before(facts, "total_assets", target, offset=1)
    total_liabilities = _instant_at_or_before(facts, "total_liabilities", target)
    operating_cash_flow = _duration_at(facts, "operating_cash_flow", target)
    capital_expenditure = _duration_at(facts, "capital_expenditure", target)

    metrics = [
        _with_sources(
            compute_revenue_growth(
                _value(revenue), _value(prior_revenue), job_id=job_id, period_end=target
            ),
            [revenue, prior_revenue],
        ),
        _with_sources(
            compute_gross_margin(
                _value(revenue), _value(gross_profit), job_id=job_id, period_end=target
            ),
            [revenue, gross_profit],
        ),
        _with_sources(
            compute_operating_margin(
                _value(revenue), _value(operating_income), job_id=job_id, period_end=target
            ),
            [revenue, operating_income],
        ),
        _with_sources(
            compute_net_margin(
                _value(revenue), _value(net_income), job_id=job_id, period_end=target
            ),
            [revenue, net_income],
        ),
        _with_sources(
            compute_net_income_growth(
                _value(net_income),
                _value(prior_net_income),
                job_id=job_id,
                period_end=target,
            ),
            [net_income, prior_net_income],
        ),
        _with_sources(
            compute_current_ratio(
                _value(current_assets),
                _value(current_liabilities),
                unit_assets=current_assets.unit if current_assets else None,
                unit_liabilities=current_liabilities.unit if current_liabilities else None,
                job_id=job_id,
                period_end=target,
            ),
            [current_assets, current_liabilities],
        ),
        _with_sources(
            compute_asset_liability_ratio(
                _value(total_liabilities),
                _value(total_assets),
                unit_liabilities=total_liabilities.unit if total_liabilities else None,
                unit_assets=total_assets.unit if total_assets else None,
                job_id=job_id,
                period_end=target,
            ),
            [total_liabilities, total_assets],
        ),
        _with_sources(
            compute_operating_cash_flow_ratio(
                _value(operating_cash_flow),
                _value(revenue),
                unit_cash_flow=operating_cash_flow.unit if operating_cash_flow else None,
                unit_revenue=revenue.unit if revenue else None,
                job_id=job_id,
                period_end=target,
            ),
            [operating_cash_flow, revenue],
        ),
        _with_sources(
            compute_free_cash_flow(
                _value(operating_cash_flow),
                _value(capital_expenditure),
                unit_cash_flow=operating_cash_flow.unit if operating_cash_flow else None,
                unit_capex=capital_expenditure.unit if capital_expenditure else None,
                job_id=job_id,
                period_end=target,
            ),
            [operating_cash_flow, capital_expenditure],
        ),
        _with_sources(
            compute_roa(
                _value(net_income),
                _value(prior_total_assets),
                _value(total_assets),
                unit_income=net_income.unit if net_income else None,
                unit_assets=total_assets.unit if total_assets else None,
                job_id=job_id,
                period_end=target,
            ),
            [net_income, prior_total_assets, total_assets],
        ),
    ]
    limitations = tuple(
        f"{metric.metric_name}: {metric.explanation}"
        for metric in metrics
        if metric.status != MetricStatus.COMPUTED and metric.explanation
    )
    return DeterministicMetricBundle(tuple(metrics), limitations)
