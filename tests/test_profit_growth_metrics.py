"""P02-15 利润与增长指标计算器契约测试（Decimal / 确定性）。

验证目标（docs/05 P02-15 验收）：
- 5 个指标：收入增长率、毛利率、营业利润率、净利率、净利润增长率；
- 正常值：计算结果精确（Decimal，无浮点误差）；
- 负数：增长率分母用 abs、利润率可为负；
- 零分母：返回 MetricStatus.NOT_COMPUTABLE 且 value 为空（禁止臆造）；
- 缺失值：返回 NOT_COMPUTABLE；
- 每个结果带 formula_version、job_id、period_end、inputs_json（可追溯）。

不修改数据库、不联网、不依赖外部服务。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.domain.models import MetricResult, MetricStatus
from invest_research.financial.metrics import (
    FORMULA_VERSION,
    compute_gross_margin,
    compute_net_income_growth,
    compute_net_margin,
    compute_operating_margin,
    compute_revenue_growth,
)

_JOB = "job-1"
_PERIOD = date(2025, 6, 30)


def _assert_computed(result: MetricResult, expected: str) -> None:
    assert result.status == MetricStatus.COMPUTED
    assert result.value == Decimal(expected)
    assert result.formula_version == FORMULA_VERSION
    assert result.job_id == _JOB
    assert result.period_end == _PERIOD


def _assert_not_computable(result: MetricResult, metric: str) -> None:
    assert result.status == MetricStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metric_name == metric
    assert result.formula_version == FORMULA_VERSION


# ---------------------------------------------------------------------------
# 收入增长率
# ---------------------------------------------------------------------------


def test_revenue_growth_normal() -> None:
    """收入增长率：本期 130 / 上期 100 → 30%。"""
    result = compute_revenue_growth(
        current=Decimal("130"), prior=Decimal("100"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_computed(result, "0.3")


def test_revenue_growth_negative_prior_uses_abs() -> None:
    """上期为负数时用 abs：本期 100 / 上期 -50 → 增长 300%（PRD 公式）。"""
    result = compute_revenue_growth(
        current=Decimal("100"), prior=Decimal("-50"), job_id=_JOB, period_end=_PERIOD
    )
    # (100 - (-50)) / abs(-50) = 150 / 50 = 3.0
    _assert_computed(result, "3.0")


def test_revenue_growth_decline() -> None:
    """收入下降：本期 80 / 上期 100 → -20%。"""
    result = compute_revenue_growth(
        current=Decimal("80"), prior=Decimal("100"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_computed(result, "-0.2")


def test_revenue_growth_zero_prior_not_computable() -> None:
    """上期为 0 → 零分母 → NOT_COMPUTABLE。"""
    result = compute_revenue_growth(
        current=Decimal("100"), prior=Decimal("0"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_not_computable(result, "revenue_growth")


# ---------------------------------------------------------------------------
# 毛利率
# ---------------------------------------------------------------------------


def test_gross_margin_normal() -> None:
    """毛利率：毛利润 40 / 收入 100 → 40%。"""
    result = compute_gross_margin(
        revenue=Decimal("100"), gross_profit=Decimal("40"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_computed(result, "0.4")


def test_gross_margin_zero_revenue_not_computable() -> None:
    """收入 0 → 零分母 → NOT_COMPUTABLE。"""
    result = compute_gross_margin(
        revenue=Decimal("0"), gross_profit=Decimal("40"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_not_computable(result, "gross_margin")


# ---------------------------------------------------------------------------
# 营业利润率
# ---------------------------------------------------------------------------


def test_operating_margin_negative() -> None:
    """营业利润为负 → 负利润率（允许为负，正确表达亏损）。"""
    result = compute_operating_margin(
        revenue=Decimal("100"), operating_income=Decimal("-10"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_computed(result, "-0.1")


def test_operating_margin_missing_revenue_not_computable() -> None:
    """收入缺失（None）→ NOT_COMPUTABLE。"""
    result = compute_operating_margin(
        revenue=None,
        operating_income=Decimal("-10"),
        job_id=_JOB,
        period_end=_PERIOD,
    )
    _assert_not_computable(result, "operating_margin")


# ---------------------------------------------------------------------------
# 净利率
# ---------------------------------------------------------------------------


def test_net_margin_normal() -> None:
    """净利率：净利润 20 / 收入 100 → 20%。"""
    result = compute_net_margin(
        revenue=Decimal("100"), net_income=Decimal("20"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_computed(result, "0.2")


def test_net_margin_zero_revenue_not_computable() -> None:
    """收入 0 → NOT_COMPUTABLE。"""
    result = compute_net_margin(
        revenue=Decimal("0"), net_income=Decimal("20"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_not_computable(result, "net_margin")


# ---------------------------------------------------------------------------
# 净利润增长率
# ---------------------------------------------------------------------------


def test_net_income_growth_from_loss_to_profit() -> None:
    """从亏损（-10）到盈利（20）：(20-(-10))/abs(-10) = 3.0 → 300%。"""
    result = compute_net_income_growth(
        current=Decimal("20"), prior=Decimal("-10"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_computed(result, "3.0")


def test_net_income_growth_zero_prior_not_computable() -> None:
    """上期净利润 0 → NOT_COMPUTABLE。"""
    result = compute_net_income_growth(
        current=Decimal("20"), prior=Decimal("0"), job_id=_JOB, period_end=_PERIOD
    )
    _assert_not_computable(result, "net_income_growth")


# ---------------------------------------------------------------------------
# 可追溯性：inputs_json 与 formula_version
# ---------------------------------------------------------------------------


def test_computed_result_records_inputs() -> None:
    """computed 结果记录 formula_version 与输入（可追溯）。"""
    result = compute_gross_margin(
        revenue=Decimal("100"), gross_profit=Decimal("40"), job_id=_JOB, period_end=_PERIOD
    )
    assert result.formula_version == FORMULA_VERSION
    assert result.inputs_json == {"revenue": "100", "gross_profit": "40"}


def test_not_computable_records_formula_version() -> None:
    """NOT_COMPUTABLE 结果同样带 formula_version（可追溯，不能丢版本）。"""
    result = compute_revenue_growth(
        current=Decimal("100"), prior=Decimal("0"), job_id=_JOB, period_end=_PERIOD
    )
    assert result.formula_version == FORMULA_VERSION
    assert result.explanation is not None
