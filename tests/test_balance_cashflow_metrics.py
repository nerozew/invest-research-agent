"""P02-16 资产负债/现金流指标计算器契约测试（Decimal / 确定性）。

验证目标（docs/05 P02-16 验收）：
- 5 个指标：流动比率、资产负债率、经营现金流率、自由现金流、ROA；
- 正常值：计算结果精确（Decimal，无浮点误差）；
- 单位冲突：输入单位不同（如 USD 与 USD/shares）→ NOT_COMPUTABLE（不可混算）；
- 零分母 / 缺失：返回 MetricStatus.NOT_COMPUTABLE 且 value 为空（禁止臆造）；
- 负数结果合法：自由现金流可为负、ROA 可为负（经营亏损）;
- 每个结果带 formula_version、job_id、period_end、inputs_json（可追溯）。

不修改数据库、不联网、不依赖外部服务。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.domain.models import MetricResult, MetricStatus
from invest_research.financial.metrics_balance import (
    FORMULA_VERSION_BALANCE,
    compute_asset_liability_ratio,
    compute_current_ratio,
    compute_free_cash_flow,
    compute_operating_cash_flow_ratio,
    compute_roa,
)

_JOB = "job-1"
_PERIOD = date(2025, 6, 30)


def _assert_computed(result: MetricResult, expected: str) -> None:
    assert result.status == MetricStatus.COMPUTED
    assert result.value == Decimal(expected)
    assert result.formula_version == FORMULA_VERSION_BALANCE
    assert result.job_id == _JOB
    assert result.period_end == _PERIOD


def _assert_not_computable(result: MetricResult, metric: str) -> None:
    assert result.status == MetricStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metric_name == metric
    assert result.formula_version == FORMULA_VERSION_BALANCE


# ---------------------------------------------------------------------------
# 流动比率
# ---------------------------------------------------------------------------


def test_current_ratio_normal() -> None:
    """流动比率：流动资产 200 / 流动负债 100 → 2.0。"""
    result = compute_current_ratio(
        current_assets=Decimal("200"),
        current_liabilities=Decimal("100"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_computed(result, "2.0")


def test_current_ratio_zero_liabilities_not_computable() -> None:
    """流动负债 0 → 零分母 → NOT_COMPUTABLE。"""
    result = compute_current_ratio(
        current_assets=Decimal("200"),
        current_liabilities=Decimal("0"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "current_ratio")


def test_current_ratio_missing_assets_not_computable() -> None:
    """流动资产缺失 → NOT_COMPUTABLE。"""
    result = compute_current_ratio(
        current_assets=None,
        current_liabilities=Decimal("100"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "current_ratio")


def test_current_ratio_unit_conflict_not_computable() -> None:
    """单位冲突（USD vs EUR）→ NOT_COMPUTABLE（不可混算）。"""
    result = compute_current_ratio(
        current_assets=Decimal("200"),
        current_liabilities=Decimal("100"),
        unit_assets="USD",
        unit_liabilities="EUR",
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "current_ratio")


# ---------------------------------------------------------------------------
# 资产负债率
# ---------------------------------------------------------------------------


def test_asset_liability_ratio_normal() -> None:
    """资产负债率：总负债 60 / 总资产 200 → 0.3。"""
    result = compute_asset_liability_ratio(
        total_liabilities=Decimal("60"),
        total_assets=Decimal("200"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_computed(result, "0.3")


def test_asset_liability_ratio_zero_assets_not_computable() -> None:
    """总资产 0 → 零分母 → NOT_COMPUTABLE。"""
    result = compute_asset_liability_ratio(
        total_liabilities=Decimal("60"),
        total_assets=Decimal("0"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "asset_liability_ratio")


def test_asset_liability_ratio_missing_liabilities_not_computable() -> None:
    """总负债缺失 → NOT_COMPUTABLE。"""
    result = compute_asset_liability_ratio(
        total_liabilities=None,
        total_assets=Decimal("200"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "asset_liability_ratio")


# ---------------------------------------------------------------------------
# 经营现金流率
# ---------------------------------------------------------------------------


def test_operating_cash_flow_ratio_normal() -> None:
    """经营现金流率：经营现金流 30 / 收入 100 → 0.3。"""
    result = compute_operating_cash_flow_ratio(
        operating_cash_flow=Decimal("30"),
        revenue=Decimal("100"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_computed(result, "0.3")


def test_operating_cash_flow_ratio_negative() -> None:
    """经营现金流为负 → 负比率（合法，表达经营现金流为负）。"""
    result = compute_operating_cash_flow_ratio(
        operating_cash_flow=Decimal("-10"),
        revenue=Decimal("100"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_computed(result, "-0.1")


def test_operating_cash_flow_ratio_zero_revenue_not_computable() -> None:
    """收入 0 → 零分母 → NOT_COMPUTABLE。"""
    result = compute_operating_cash_flow_ratio(
        operating_cash_flow=Decimal("30"),
        revenue=Decimal("0"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "operating_cash_flow_ratio")


# ---------------------------------------------------------------------------
# 自由现金流（绝对值指标，非比率）
# ---------------------------------------------------------------------------


def test_free_cash_flow_normal() -> None:
    """自由现金流：经营现金流 100 - 资本性支出 40 → 60。"""
    result = compute_free_cash_flow(
        operating_cash_flow=Decimal("100"),
        capital_expenditure=Decimal("40"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_computed(result, "60")


def test_free_cash_flow_negative() -> None:
    """自由现金流可为负：经营现金流 30 - 资本性支出 50 → -20。"""
    result = compute_free_cash_flow(
        operating_cash_flow=Decimal("30"),
        capital_expenditure=Decimal("50"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_computed(result, "-20")


def test_free_cash_flow_missing_capex_not_computable() -> None:
    """资本性支出缺失 → NOT_COMPUTABLE。"""
    result = compute_free_cash_flow(
        operating_cash_flow=Decimal("100"),
        capital_expenditure=None,
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "free_cash_flow")


# ---------------------------------------------------------------------------
# ROA（净利润 / 平均总资产）
# ---------------------------------------------------------------------------


def test_roa_normal() -> None:
    """ROA：净利润 20 / 平均总资产 ((180+220)/2=200) → 0.1。"""
    result = compute_roa(
        net_income=Decimal("20"),
        total_assets_begin=Decimal("180"),
        total_assets_end=Decimal("220"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_computed(result, "0.1")


def test_roa_negative() -> None:
    """ROA 可为负（经营亏损）：净利润 -10 / 平均总资产 200 → -0.05。"""
    result = compute_roa(
        net_income=Decimal("-10"),
        total_assets_begin=Decimal("180"),
        total_assets_end=Decimal("220"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_computed(result, "-0.05")


def test_roa_zero_total_assets_not_computable() -> None:
    """期初+期末总资产都为 0 → 平均总资产 0 → NOT_COMPUTABLE。"""
    result = compute_roa(
        net_income=Decimal("20"),
        total_assets_begin=Decimal("0"),
        total_assets_end=Decimal("0"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "roa")


def test_roa_missing_assets_not_computable() -> None:
    """总资产缺失 → NOT_COMPUTABLE。"""
    result = compute_roa(
        net_income=Decimal("20"),
        total_assets_begin=None,
        total_assets_end=Decimal("220"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "roa")


# ---------------------------------------------------------------------------
# 可追溯性
# ---------------------------------------------------------------------------


def test_computed_result_records_inputs() -> None:
    """computed 结果记录 formula_version 与输入（可追溯）。"""
    result = compute_current_ratio(
        current_assets=Decimal("200"),
        current_liabilities=Decimal("100"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    assert result.formula_version == FORMULA_VERSION_BALANCE
    assert result.inputs_json == {"current_assets": "200", "current_liabilities": "100"}


def test_not_computable_records_formula_version() -> None:
    """NOT_COMPUTABLE 结果同样带 formula_version（可追溯，不能丢版本）。"""
    result = compute_current_ratio(
        current_assets=Decimal("200"),
        current_liabilities=Decimal("0"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    assert result.formula_version == FORMULA_VERSION_BALANCE
    assert result.explanation is not None
