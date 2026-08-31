"""P02-15 利润与增长指标计算器（Decimal / 确定性版本化公式）。

实现 PRD §8 的 5 个利润/增长指标公式（版本：profit_growth_metrics_v1）：
- 收入增长率   = (本期收入 - 上期收入) / abs(上期收入)
- 毛利率       = 毛利润 / 收入
- 营业利润率   = 营业利润 / 收入
- 净利率       = 净利润 / 收入
- 净利润增长率 = (本期净利润 - 上期净利润) / abs(上期净利润)

规则：
- 使用 Decimal，禁止 float（财务算术不得有二进制浮点误差）；
- 零分母 / 缺失输入 → MetricStatus.NOT_COMPUTABLE 且 value=None（PRD：禁止臆造数值）；
- 每个结果携带 formula_version、job_id、period_end、inputs_json、explanation（可追溯）。

依赖边界：只依赖标准库与 domain 层（MetricResult/MetricStatus）；
禁止导入 CrewAI/FastAPI/SQLAlchemy/httpx。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.domain.models import MetricResult, MetricStatus

FORMULA_VERSION = "profit_growth_metrics_v1"


def _not_computable(
    metric_name: str,
    job_id: str,
    period_end: date,
    inputs_json: dict[str, str],
    reason: str,
) -> MetricResult:
    return MetricResult(
        job_id=job_id,
        metric_name=metric_name,
        period_end=period_end,
        value=None,
        unit="ratio",
        status=MetricStatus.NOT_COMPUTABLE,
        formula_version=FORMULA_VERSION,
        inputs_json=inputs_json,
        explanation=reason,
    )


def _computed(
    metric_name: str,
    job_id: str,
    period_end: date,
    inputs_json: dict[str, str],
    value: Decimal,
) -> MetricResult:
    return MetricResult(
        job_id=job_id,
        metric_name=metric_name,
        period_end=period_end,
        value=value,
        unit="ratio",
        status=MetricStatus.COMPUTED,
        formula_version=FORMULA_VERSION,
        inputs_json=inputs_json,
    )


def compute_revenue_growth(
    current: Decimal | None,
    prior: Decimal | None,
    *,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """收入增长率：``(current - prior) / abs(prior)``。上期缺失或为 0 → NOT_COMPUTABLE。"""
    inputs = {
        "current": str(current) if current is not None else "",
        "prior": str(prior) if prior is not None else "",
    }
    if current is None or prior is None:
        return _not_computable("revenue_growth", job_id, period_end, inputs, "收入输入缺失")
    if prior == 0:
        return _not_computable(
            "revenue_growth", job_id, period_end, inputs, "上期收入为 0，增长率不可计算"
        )
    value = (current - prior) / abs(prior)
    return _computed("revenue_growth", job_id, period_end, inputs, value)


def compute_gross_margin(
    revenue: Decimal | None,
    gross_profit: Decimal | None,
    *,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """毛利率：``gross_profit / revenue``。收入缺失或为 0 → NOT_COMPUTABLE。"""
    inputs = {
        "revenue": str(revenue) if revenue is not None else "",
        "gross_profit": str(gross_profit) if gross_profit is not None else "",
    }
    if revenue is None or gross_profit is None:
        return _not_computable("gross_margin", job_id, period_end, inputs, "毛利率输入缺失")
    if revenue == 0:
        return _not_computable(
            "gross_margin", job_id, period_end, inputs, "收入为 0，毛利率不可计算"
        )
    value = gross_profit / revenue
    return _computed("gross_margin", job_id, period_end, inputs, value)


def compute_operating_margin(
    revenue: Decimal | None,
    operating_income: Decimal | None,
    *,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """营业利润率：``operating_income / revenue``（允许为负）。收入缺失或为 0 → NOT_COMPUTABLE。"""
    inputs = {
        "revenue": str(revenue) if revenue is not None else "",
        "operating_income": str(operating_income) if operating_income is not None else "",
    }
    if revenue is None or operating_income is None:
        return _not_computable("operating_margin", job_id, period_end, inputs, "营业利润率输入缺失")
    if revenue == 0:
        return _not_computable(
            "operating_margin", job_id, period_end, inputs, "收入为 0，营业利润率不可计算"
        )
    value = operating_income / revenue
    return _computed("operating_margin", job_id, period_end, inputs, value)


def compute_net_margin(
    revenue: Decimal | None,
    net_income: Decimal | None,
    *,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """净利率：``net_income / revenue``。收入缺失或为 0 → NOT_COMPUTABLE。"""
    inputs = {
        "revenue": str(revenue) if revenue is not None else "",
        "net_income": str(net_income) if net_income is not None else "",
    }
    if revenue is None or net_income is None:
        return _not_computable("net_margin", job_id, period_end, inputs, "净利率输入缺失")
    if revenue == 0:
        return _not_computable("net_margin", job_id, period_end, inputs, "收入为 0，净利率不可计算")
    value = net_income / revenue
    return _computed("net_margin", job_id, period_end, inputs, value)


def compute_net_income_growth(
    current: Decimal | None,
    prior: Decimal | None,
    *,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """净利润增长率：``(current - prior) / abs(prior)``。上期缺失或为 0 → NOT_COMPUTABLE。"""
    inputs = {
        "current": str(current) if current is not None else "",
        "prior": str(prior) if prior is not None else "",
    }
    if current is None or prior is None:
        return _not_computable("net_income_growth", job_id, period_end, inputs, "净利润输入缺失")
    if prior == 0:
        return _not_computable(
            "net_income_growth", job_id, period_end, inputs, "上期净利润为 0，增长率不可计算"
        )
    value = (current - prior) / abs(prior)
    return _computed("net_income_growth", job_id, period_end, inputs, value)
