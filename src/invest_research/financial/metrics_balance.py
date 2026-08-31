"""P02-16 资产负债/现金流指标计算器（Decimal / 确定性版本化公式）。

实现 PRD §8 的 5 个资产负债/现金流指标（版本：balance_cashflow_metrics_v1）：
- 流动比率     = 流动资产 / 流动负债
- 资产负债率   = 总负债 / 总资产
- 经营现金流率 = 经营活动现金流 / 收入
- 自由现金流   = 经营活动现金流 - 资本性支出
- ROA         = 净利润 / 平均总资产（=(期初+期末)/2）

规则：
- 使用 Decimal，禁止 float；
- 零分母 / 缺失输入 → NOT_COMPUTABLE 且 value=None（禁止臆造数值）；
- 单位冲突：ratio 类输入若同时提供单位且不一致（如 USD vs EUR）→ NOT_COMPUTABLE；
- 负值合法：ROA、经营现金流率、自由现金流可为负（亏损/现金流为负是正常业务）；
- 每个结果携带 formula_version、job_id、period_end、inputs_json。

依赖边界：只依赖标准库与 domain 层；禁止导入 CrewAI/FastAPI/SQLAlchemy/httpx。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.domain.models import MetricResult, MetricStatus

FORMULA_VERSION_BALANCE = "balance_cashflow_metrics_v1"


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
        formula_version=FORMULA_VERSION_BALANCE,
        inputs_json=inputs_json,
        explanation=reason,
    )


def _computed(
    metric_name: str,
    job_id: str,
    period_end: date,
    inputs_json: dict[str, str],
    value: Decimal,
    unit: str = "ratio",
) -> MetricResult:
    return MetricResult(
        job_id=job_id,
        metric_name=metric_name,
        period_end=period_end,
        value=value,
        unit=unit,
        status=MetricStatus.COMPUTED,
        formula_version=FORMULA_VERSION_BALANCE,
        inputs_json=inputs_json,
    )


def _normalize_unit(unit: str | None) -> str | None:
    """归一化单位：去空白、小写；空串视为 None。"""
    if unit is None:
        return None
    cleaned = unit.strip().lower()
    return cleaned or None


def _units_conflict(unit_a: str | None, unit_b: str | None) -> bool:
    """两个单位都提供且不一致 → True（冲突）；任一缺失 → False（不校验）。"""
    ua, ub = _normalize_unit(unit_a), _normalize_unit(unit_b)
    return ua is not None and ub is not None and ua != ub


def compute_current_ratio(
    current_assets: Decimal | None,
    current_liabilities: Decimal | None,
    *,
    unit_assets: str | None = None,
    unit_liabilities: str | None = None,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """流动比率：流动资产/流动负债。负债为 0、缺失或单位冲突 → NOT_COMPUTABLE。"""
    inputs = {
        "current_assets": str(current_assets) if current_assets is not None else "",
        "current_liabilities": str(current_liabilities) if current_liabilities is not None else "",
    }
    reason = None
    if current_assets is None or current_liabilities is None:
        reason = "流动比率输入缺失"
    elif _units_conflict(unit_assets, unit_liabilities):
        reason = "流动比率输入单位不一致，不可混算"
    elif current_liabilities == 0:
        reason = "流动负债为 0，流动比率不可计算"
    if reason is not None:
        return _not_computable("current_ratio", job_id, period_end, inputs, reason)
    value = current_assets / current_liabilities  # type: ignore[operator]  # 已排除 None
    return _computed("current_ratio", job_id, period_end, inputs, value)


def compute_asset_liability_ratio(
    total_liabilities: Decimal | None,
    total_assets: Decimal | None,
    *,
    unit_liabilities: str | None = None,
    unit_assets: str | None = None,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """资产负债率：总负债/总资产。资产为 0、缺失或单位冲突 → NOT_COMPUTABLE。"""
    inputs = {
        "total_liabilities": str(total_liabilities) if total_liabilities is not None else "",
        "total_assets": str(total_assets) if total_assets is not None else "",
    }
    reason = None
    if total_liabilities is None or total_assets is None:
        reason = "资产负债率输入缺失"
    elif _units_conflict(unit_liabilities, unit_assets):
        reason = "资产负债率输入单位不一致，不可混算"
    elif total_assets == 0:
        reason = "总资产为 0，资产负债率不可计算"
    if reason is not None:
        return _not_computable("asset_liability_ratio", job_id, period_end, inputs, reason)
    value = total_liabilities / total_assets  # type: ignore[operator]  # 已排除 None
    return _computed("asset_liability_ratio", job_id, period_end, inputs, value)


def compute_operating_cash_flow_ratio(
    operating_cash_flow: Decimal | None,
    revenue: Decimal | None,
    *,
    unit_cash_flow: str | None = None,
    unit_revenue: str | None = None,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """经营现金流率：经营现金流/收入（可为负）。收入 0、缺失或单位冲突 → NOT_COMPUTABLE。"""
    inputs = {
        "operating_cash_flow": str(operating_cash_flow) if operating_cash_flow is not None else "",
        "revenue": str(revenue) if revenue is not None else "",
    }
    reason = None
    if operating_cash_flow is None or revenue is None:
        reason = "经营现金流率输入缺失"
    elif _units_conflict(unit_cash_flow, unit_revenue):
        reason = "经营现金流率输入单位不一致，不可混算"
    elif revenue == 0:
        reason = "收入为 0，经营现金流率不可计算"
    if reason is not None:
        return _not_computable("operating_cash_flow_ratio", job_id, period_end, inputs, reason)
    value = operating_cash_flow / revenue  # type: ignore[operator]  # 已排除 None
    return _computed("operating_cash_flow_ratio", job_id, period_end, inputs, value)


def compute_free_cash_flow(
    operating_cash_flow: Decimal | None,
    capital_expenditure: Decimal | None,
    *,
    unit_cash_flow: str | None = None,
    unit_capex: str | None = None,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """自由现金流：经营现金流-资本性支出（绝对值，可为负）。缺失/单位冲突 → NOT_COMPUTABLE。"""
    inputs = {
        "operating_cash_flow": str(operating_cash_flow) if operating_cash_flow is not None else "",
        "capital_expenditure": str(capital_expenditure) if capital_expenditure is not None else "",
    }
    reason = None
    if operating_cash_flow is None or capital_expenditure is None:
        reason = "自由现金流输入缺失"
    elif _units_conflict(unit_cash_flow, unit_capex):
        reason = "自由现金流输入单位不一致，不可混算"
    if reason is not None:
        return _not_computable("free_cash_flow", job_id, period_end, inputs, reason)
    value = operating_cash_flow - capital_expenditure  # type: ignore[operator]  # 已排除 None
    return _computed("free_cash_flow", job_id, period_end, inputs, value, unit="USD")


def compute_roa(
    net_income: Decimal | None,
    total_assets_begin: Decimal | None,
    total_assets_end: Decimal | None,
    *,
    unit_income: str | None = None,
    unit_assets: str | None = None,
    job_id: str,
    period_end: date,
) -> MetricResult:
    """ROA：净利润/平均总资产（可为负）。资产缺失、平均为 0 或单位冲突 → NOT_COMPUTABLE。"""
    inputs = {
        "net_income": str(net_income) if net_income is not None else "",
        "total_assets_begin": str(total_assets_begin) if total_assets_begin is not None else "",
        "total_assets_end": str(total_assets_end) if total_assets_end is not None else "",
    }
    reason = None
    if net_income is None or total_assets_begin is None or total_assets_end is None:
        reason = "ROA 输入缺失"
    elif _units_conflict(unit_income, unit_assets):
        reason = "ROA 输入单位不一致，不可混算"
    else:
        avg_assets = (total_assets_begin + total_assets_end) / Decimal("2")
        if avg_assets == 0:
            reason = "平均总资产为 0，ROA 不可计算"
        else:
            value = net_income / avg_assets
            return _computed("roa", job_id, period_end, inputs, value)
    return _not_computable("roa", job_id, period_end, inputs, reason)
