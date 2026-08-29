"""P06-11M：财务指标确定性装配与 Analysis 工具 Schema 回归测试。

全程使用本地构造的 SEC 风格事实，不调用真实 LLM/SEC/Serper。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from invest_research.agents.analysis_task import financial_calculator, financial_fact_query
from invest_research.application.analysis_assembler import AnalysisPackAssembler, build_fact_ref
from invest_research.domain.models import (
    AnalysisCompleteness,
    AnalysisSelectionDraft,
    FinancialFact,
    MetricStatus,
)
from invest_research.infrastructure.real_tools import _serialize_facts


def _duration(
    concept: str,
    value: str,
    start: date,
    end: date,
    *,
    fiscal_year: int,
) -> FinancialFact:
    return FinancialFact(
        company_id="0000000001",
        source_id="sec-companyfacts-0000000001",
        taxonomy="us-gaap",
        concept=concept,
        value=Decimal(value),
        unit="USD",
        period_start=start,
        period_end=end,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        form_type="10-K",
        accession_number=f"{fiscal_year}-annual",
    )


def _instant(concept: str, value: str, when: date, *, fiscal_year: int) -> FinancialFact:
    return FinancialFact(
        company_id="0000000001",
        source_id="sec-companyfacts-0000000001",
        taxonomy="us-gaap",
        concept=concept,
        value=Decimal(value),
        unit="USD",
        instant_date=when,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        form_type="10-K",
        accession_number=f"{fiscal_year}-annual",
    )


def _quarter(
    concept: str,
    value: str,
    start: date,
    end: date,
    *,
    fiscal_year: int,
    fiscal_period: str = "Q1",
) -> FinancialFact:
    return FinancialFact(
        company_id="0000000001",
        source_id="sec-companyfacts-0000000001",
        taxonomy="us-gaap",
        concept=concept,
        value=Decimal(value),
        unit="USD",
        period_start=start,
        period_end=end,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        form_type="10-Q",
        accession_number=f"{fiscal_year}-{fiscal_period.lower()}",
    )


def _quarter_instant(
    concept: str,
    value: str,
    when: date,
    *,
    fiscal_year: int,
    fiscal_period: str = "Q1",
) -> FinancialFact:
    return FinancialFact(
        company_id="0000000001",
        source_id="sec-companyfacts-0000000001",
        taxonomy="us-gaap",
        concept=concept,
        value=Decimal(value),
        unit="USD",
        instant_date=when,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        form_type="10-Q",
        accession_number=f"{fiscal_year}-{fiscal_period.lower()}",
    )


def _two_year_facts() -> list[FinancialFact]:
    current_start, current_end = date(2024, 1, 1), date(2024, 12, 31)
    prior_start, prior_end = date(2023, 1, 1), date(2023, 12, 31)
    return [
        _duration("Revenues", "100", current_start, current_end, fiscal_year=2024),
        _duration("Revenues", "80", prior_start, prior_end, fiscal_year=2023),
        _duration("GrossProfit", "40", current_start, current_end, fiscal_year=2024),
        _duration("OperatingIncomeLoss", "20", current_start, current_end, fiscal_year=2024),
        _duration("NetIncomeLoss", "10", current_start, current_end, fiscal_year=2024),
        _duration("NetIncomeLoss", "8", prior_start, prior_end, fiscal_year=2023),
        _duration(
            "NetCashProvidedByUsedInOperatingActivities",
            "15",
            current_start,
            current_end,
            fiscal_year=2024,
        ),
        _duration(
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "5",
            current_start,
            current_end,
            fiscal_year=2024,
        ),
        _instant("AssetsCurrent", "60", current_end, fiscal_year=2024),
        _instant("LiabilitiesCurrent", "30", current_end, fiscal_year=2024),
        _instant("Assets", "200", current_end, fiscal_year=2024),
        _instant("Assets", "160", prior_end, fiscal_year=2023),
        _instant("Liabilities", "100", current_end, fiscal_year=2024),
    ]


def test_analysis_tool_schemas_are_buildable_and_explicit() -> None:
    """CrewAI/Pydantic 必须能直接生成两个工具的 JSON Schema。"""
    query_schema = financial_fact_query.args_schema.model_json_schema()
    calculator_schema = financial_calculator.args_schema.model_json_schema()

    assert "period_end" in query_schema["properties"]
    assert "period_end" in calculator_schema["properties"]
    assert "inputs" not in calculator_schema["properties"]
    assert {"revenue", "gross_profit", "current", "prior"}.issubset(
        calculator_schema["properties"]
    )


def test_financial_calculator_accepts_iso_date_from_tool_json() -> None:
    result = financial_calculator.run(
        metric_name="gross_margin",
        job_id="job-1",
        period_end="2024-12-31",
        revenue="100",
        gross_profit="40",
    )
    assert result["status"] == MetricStatus.COMPUTED.value
    assert Decimal(str(result["value"])) == Decimal("0.4")


def test_ten_k_serialization_keeps_two_annual_periods_not_later_quarter() -> None:
    """请求 10-K 时，不能让更晚的 10-Q 挤掉上一财年的年度可比值。"""
    facts = _two_year_facts()
    facts.append(
        FinancialFact(
            company_id="0000000001",
            source_id="sec-companyfacts-0000000001",
            taxonomy="us-gaap",
            concept="Revenues",
            value=Decimal("30"),
            unit="USD",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 3, 31),
            fiscal_year=2025,
            fiscal_period="Q1",
            form_type="10-Q",
            accession_number="2025-q1",
        )
    )
    result = SimpleNamespace(kind="success", value=SimpleNamespace(facts=facts))

    import json

    payload = json.loads(
        _serialize_facts(result, "2025-10-31", requested_forms=("10-K",))
    )
    revenues = [item for item in payload["facts"] if item["metric_name"] == "revenue"]
    assert [item["period_end"] for item in revenues] == ["2024-12-31", "2023-12-31"]


def test_mixed_forms_keep_annual_pair_without_losing_latest_interim() -> None:
    """同时请求 10-K/10-Q 时，年度对与最新中期事实必须同时保留。"""
    facts = [
        _duration(
            "Revenues",
            "120",
            date(2024, 9, 29),
            date(2025, 9, 27),
            fiscal_year=2025,
        ),
        FinancialFact(
            company_id="0000000001",
            source_id="sec-companyfacts-0000000001",
            taxonomy="us-gaap",
            concept="Revenues",
            value=Decimal("90"),
            unit="USD",
            period_start=date(2024, 9, 29),
            period_end=date(2025, 6, 28),
            fiscal_year=2025,
            fiscal_period="Q3",
            form_type="10-Q",
            accession_number="2025-q3",
        ),
        _duration(
            "Revenues",
            "100",
            date(2023, 10, 1),
            date(2024, 9, 28),
            fiscal_year=2024,
        ),
    ]
    result = SimpleNamespace(kind="success", value=SimpleNamespace(facts=facts))

    import json

    payload = json.loads(
        _serialize_facts(result, "2025-10-31", requested_forms=("10-K", "10-Q"))
    )
    revenues = [item for item in payload["facts"] if item["metric_name"] == "revenue"]
    assert [item["period_end"] for item in revenues] == [
        "2025-09-27",
        "2024-09-28",
        "2025-06-28",
    ]


def test_microsoft_fiscal_calendar_keeps_fy_pair_after_new_q1_is_filed() -> None:
    """6 月财年结束且 9 月新 Q1 已发布时，Q1 不能挤掉 10-K 年度事实。"""
    facts = [
        _duration("Revenues", "281724", date(2024, 7, 1), date(2025, 6, 30), fiscal_year=2025),
        _duration("Revenues", "245122", date(2023, 7, 1), date(2024, 6, 30), fiscal_year=2024),
        _quarter("Revenues", "77673", date(2025, 7, 1), date(2025, 9, 30), fiscal_year=2026),
        _quarter("Revenues", "65585", date(2024, 7, 1), date(2024, 9, 30), fiscal_year=2025),
        _instant("Assets", "619003", date(2025, 6, 30), fiscal_year=2025),
        _instant("Assets", "512163", date(2024, 6, 30), fiscal_year=2024),
        _quarter_instant("Assets", "636351", date(2025, 9, 30), fiscal_year=2026),
        _quarter_instant("Assets", "523013", date(2024, 9, 30), fiscal_year=2025),
    ]
    result = SimpleNamespace(kind="success", value=SimpleNamespace(facts=facts))

    import json

    payload = json.loads(
        _serialize_facts(result, "2025-10-31", requested_forms=("10-K", "10-Q"))
    )
    revenues = [item for item in payload["facts"] if item["metric_name"] == "revenue"]
    assets = [item for item in payload["facts"] if item["metric_name"] == "total_assets"]

    assert [(item["form_type"], item["period_end"]) for item in revenues] == [
        ("10-K", "2025-06-30"),
        ("10-K", "2024-06-30"),
        ("10-Q", "2025-09-30"),
        ("10-Q", "2024-09-30"),
    ]
    assert [(item["form_type"], item["instant_date"]) for item in assets] == [
        ("10-K", "2025-06-30"),
        ("10-K", "2024-06-30"),
        ("10-Q", "2025-09-30"),
        ("10-Q", "2024-09-30"),
    ]


def test_quarter_ytd_selects_same_fiscal_period_from_previous_year() -> None:
    """截至年中时，最新 Q3 YTD 必须配上一财年 Q3 YTD，而不是本年 Q2。"""
    facts = [
        FinancialFact(
            company_id="0000000001",
            source_id="sec-companyfacts-0000000001",
            taxonomy="us-gaap",
            concept="Revenues",
            value=Decimal("90"),
            unit="USD",
            period_start=date(2024, 9, 29),
            period_end=date(2025, 6, 28),
            fiscal_year=2025,
            fiscal_period="Q3",
            form_type="10-Q",
            accession_number="2025-q3",
        ),
        FinancialFact(
            company_id="0000000001",
            source_id="sec-companyfacts-0000000001",
            taxonomy="us-gaap",
            concept="Revenues",
            value=Decimal("55"),
            unit="USD",
            period_start=date(2024, 9, 29),
            period_end=date(2025, 3, 29),
            fiscal_year=2025,
            fiscal_period="Q2",
            form_type="10-Q",
            accession_number="2025-q2",
        ),
        FinancialFact(
            company_id="0000000001",
            source_id="sec-companyfacts-0000000001",
            taxonomy="us-gaap",
            concept="Revenues",
            value=Decimal("80"),
            unit="USD",
            period_start=date(2023, 10, 1),
            period_end=date(2024, 6, 29),
            fiscal_year=2024,
            fiscal_period="Q3",
            form_type="10-Q",
            accession_number="2024-q3",
        ),
    ]
    result = SimpleNamespace(kind="success", value=SimpleNamespace(facts=facts))

    import json

    payload = json.loads(
        _serialize_facts(result, "2025-07-01", requested_forms=("10-K", "10-Q"))
    )
    revenues = [item for item in payload["facts"] if item["metric_name"] == "revenue"]
    assert [item["period_end"] for item in revenues] == ["2025-06-28", "2024-06-29"]


def test_instant_facts_select_latest_year_end_and_previous_year_end() -> None:
    """时点指标不能让本年 Q3 挤掉 ROA 所需的上一年度期末资产。"""
    facts = [
        _instant("Assets", "220", date(2025, 9, 27), fiscal_year=2025),
        FinancialFact(
            company_id="0000000001",
            source_id="sec-companyfacts-0000000001",
            taxonomy="us-gaap",
            concept="Assets",
            value=Decimal("210"),
            unit="USD",
            instant_date=date(2025, 6, 28),
            fiscal_year=2025,
            fiscal_period="Q3",
            form_type="10-Q",
            accession_number="2025-q3",
        ),
        _instant("Assets", "200", date(2024, 9, 28), fiscal_year=2024),
    ]
    result = SimpleNamespace(kind="success", value=SimpleNamespace(facts=facts))

    import json

    payload = json.loads(
        _serialize_facts(result, "2025-10-31", requested_forms=("10-K", "10-Q"))
    )
    assets = [item for item in payload["facts"] if item["metric_name"] == "total_assets"]
    assert [item["instant_date"] for item in assets] == [
        "2025-09-27",
        "2024-09-28",
        "2025-06-28",
    ]


def test_assembler_computes_all_required_metrics_from_trusted_source_facts() -> None:
    """LLM 只选事实；10 个指标必须由本地 Decimal 公式确定性生成。"""
    facts = _two_year_facts()
    current_revenue = facts[0]
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 12, 31),
        selected_fact_refs=[build_fact_ref(current_revenue)],
        metric_results=[],
        completeness=AnalysisCompleteness.COMPLETE,
    )

    pack = AnalysisPackAssembler().assemble(draft, facts, job_id="job-42")
    by_name = {metric.metric_name: metric for metric in pack.metrics}

    assert len(by_name) == 10
    assert all(metric.status == MetricStatus.COMPUTED for metric in by_name.values())
    assert by_name["revenue_growth"].value == Decimal("0.25")
    assert by_name["gross_margin"].value == Decimal("0.4")
    assert by_name["free_cash_flow"].value == Decimal("10")
    assert by_name["roa"].value == Decimal("0.05555555555555555555555555556")
    assert all(metric.job_id == "job-42" for metric in by_name.values())
    assert all(metric.inputs_json.get("source_facts") for metric in by_name.values())


def test_assembler_removes_finalizer_placeholder_but_keeps_real_limitations() -> None:
    """模板占位符不能污染最终报告，但真实业务限制必须原样保留。"""
    facts = _two_year_facts()
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 12, 31),
        selected_fact_refs=[build_fact_ref(facts[0])],
        metric_results=[],
        completeness=AnalysisCompleteness.COMPLETE,
        limitations=[
            "部分数据不可用；请在此说明缺失项及原因",
            "公司分部收入缺少统一口径，未纳入本次指标计算",
        ],
    )

    pack = AnalysisPackAssembler().assemble(draft, facts, job_id="job-43")

    assert pack.limitations == ["公司分部收入缺少统一口径，未纳入本次指标计算"]
