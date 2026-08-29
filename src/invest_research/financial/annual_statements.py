"""三张财务报表的确定性导出（资产负债表 / 利润表 / 现金流量表）。

数据来源是 SEC Company Facts 全量 ``FinancialFact``：三张表的每个行项目
本来就是带标签的结构化数字，本模块按行项目映射（``statements_v1.json``）
筛出 target（可选 comparator）财年的值，组装为 ``FinancialStatementSet``。

保真边界：
- 数字直取 ``FinancialFact.value``，**不经任何 LLM**；
- 复用 ``annual_comparison`` 的严格筛选模式（财年 + FY 期间 + 10-K 表单 +
  accession + instant/duration），单概念多值冲突拒绝，缺失行降级为
  ``value=None`` 并记 limitation，不阻塞整表。

依赖边界：仅标准库、Pydantic、domain、financial 层；禁止导入 CrewAI/SQLAlchemy。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from invest_research.domain.models import FinancialFact
from invest_research.financial.annual_comparison import _select_one

STATEMENTS_V1_PATH = Path(__file__).parent / "mappings" / "statements_v1.json"


class StatementPeriodType(StrEnum):
    """报表行项目的期间类型：时点（资产负债表）或期间（利润/现金流）。"""

    INSTANT = "instant"
    DURATION = "duration"


class FinancialStatementKind(StrEnum):
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW = "cash_flow"


_DERIVE_FORMULAS: dict[str, Callable[[Decimal, Decimal], Decimal]] = {
    "add": lambda a, b: a + b,
    "subtract": lambda a, b: a - b,
    "multiply": lambda a, b: a * b,
    "divide": lambda a, b: a / b,
}


class DerivationRule(BaseModel):
    """行项目的确定性推导规则（候选 concept 缺失时的兜底）。

    ``inputs`` 引用同一 statement mapping 内其他行的 ``label``（如"收入"/"销售成本"），
    运行时从已算行取值；公式为白名单四则运算（不引入任意函数）。
    """

    model_config = ConfigDict(frozen=True)

    formula: Literal["add", "subtract", "multiply", "divide"]
    inputs: tuple[str, ...] = Field(min_length=2, max_length=2)


class StatementRowDef(BaseModel):
    """单个行项目的映射定义（label + concept 候选优先级 + 可选推导）。"""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    candidates: tuple[str, ...] = Field(min_length=1)
    derivation: DerivationRule | None = None


class StatementDef(BaseModel):
    """一张表的映射定义（表名 + 期间类型 + 行项目）。"""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    period_type: StatementPeriodType
    rows: tuple[StatementRowDef, ...] = Field(min_length=1)


class StatementMapping(BaseModel):
    """版本化三张表行项目映射集合。"""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)
    statements: dict[str, StatementDef] = Field(default_factory=dict)


class BalanceCheckResult(BaseModel):
    """资产负债表勾稽校验结果（可算才校验）。"""

    model_config = ConfigDict(frozen=True)

    ok: bool
    message: str | None = None


class FinancialStatementRow(BaseModel):
    """报表单行：label + 命中 concept + target/comparator 值（数字原封不动）。

    ``derivation_source`` 非空表示该值由确定性推导得到（候选 concept 缺失时，
    用 ``inputs`` 对应行做四则运算），数字仍是代码按 SEC 事实算的，不经 LLM。
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    concept: str | None = None
    unit: str | None = None
    period_type: StatementPeriodType
    period_end: date | None = None
    accession_number: str | None = None
    value: Decimal | None = None
    comparator_value: Decimal | None = None
    derivation_source: tuple[str, ...] | None = None


class FinancialStatementSet(BaseModel):
    """一张完整报表的确定性导出结果。"""

    model_config = ConfigDict(frozen=True)

    kind: FinancialStatementKind
    label: str = Field(min_length=1)
    target_year: int = Field(ge=1900, le=9999)
    comparator_year: int | None = None
    rows: tuple[FinancialStatementRow, ...] = ()
    limitations: tuple[str, ...] = ()
    balance_check: BalanceCheckResult | None = None


def load_statement_mapping(path: Path = STATEMENTS_V1_PATH) -> StatementMapping:
    """从 JSON 数据文件加载并校验三张表映射（fail-fast）。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return StatementMapping.model_validate(raw)


def _derive_row_value(
    derivation: DerivationRule,
    row_by_label: dict[str, FinancialStatementRow],
    *,
    comparator: bool,
) -> tuple[Decimal | None, bool]:
    """按推导规则从同表已算行取值；任何输入缺失/运算失败返回 ``(None, False)``。

    确定性：只做白名单四则运算（``_DERIVE_FORMULAS``），数字是 SEC 事实的
    加减乘除，不引入 LLM 或任意函数。
    """
    values: list[Decimal] = []
    for in_label in derivation.inputs:
        in_row = row_by_label.get(in_label)
        if in_row is None:
            return None, False
        v = in_row.comparator_value if comparator else in_row.value
        if v is None:
            return None, False
        values.append(v)
    fn = _DERIVE_FORMULAS[derivation.formula]
    try:
        result = fn(*values)
    except (ZeroDivisionError, InvalidOperation, TypeError):
        return None, False
    return Decimal(result), True


def extract_statements(
    facts: list[FinancialFact],
    mapping: StatementMapping,
    *,
    target_year: int,
    target_accession: str,
    target_report_date: date | None = None,
    comparator_year: int | None = None,
    comparator_accession: str | None = None,
    comparator_report_date: date | None = None,
) -> tuple[FinancialStatementSet, ...]:
    """确定性导出三张表：按行项目映射筛出 target（可选 comparator）财年值。

    缺失行 → ``value=None`` + limitation（不阻塞）；单概念多值冲突 → 丢弃该行。
    """
    sets: list[FinancialStatementSet] = []
    all_limitations: list[str] = []
    for kind in FinancialStatementKind:
        definition = mapping.statements.get(kind.value)
        if definition is None:
            continue
        instant = definition.period_type is StatementPeriodType.INSTANT
        rows: list[FinancialStatementRow] = []
        row_by_label: dict[str, FinancialStatementRow] = {}
        # 第一遍：尝试 concept 候选（现有逻辑）。
        for row_def in definition.rows:
            target = _select_one(
                facts,
                candidates=row_def.candidates,
                fiscal_year=target_year,
                accession=target_accession,
                report_date=target_report_date,
                instant=instant,
                logical_name=row_def.label,
            )
            comparator = None
            if comparator_accession is not None and comparator_year is not None:
                comparator = _select_one(
                    facts,
                    candidates=row_def.candidates,
                    fiscal_year=comparator_year,
                    accession=comparator_accession,
                    report_date=comparator_report_date,
                    instant=instant,
                    logical_name=row_def.label,
                )
            if target.limitation:
                all_limitations.append(f"{kind.value}.{row_def.label}: {target.limitation}")
            if comparator is not None and comparator.limitation:
                all_limitations.append(f"{kind.value}.{row_def.label}: {comparator.limitation}")
            fact = target.fact if target.fact is not None else (
                comparator.fact if comparator is not None else None
            )
            row = FinancialStatementRow(
                label=row_def.label,
                concept=fact.concept if fact is not None else None,
                unit=fact.unit if fact is not None else None,
                period_type=definition.period_type,
                period_end=(
                    fact.period_end if fact is not None and fact.period_end is not None
                    else (fact.instant_date if fact is not None else None)
                ),
                accession_number=fact.accession_number if fact is not None else None,
                value=target.fact.value if target.fact is not None else None,
                comparator_value=(
                    comparator.fact.value
                    if comparator is not None and comparator.fact is not None
                    else None
                ),
            )
            row_by_label[row_def.label] = row
            rows.append(row)
        # 第二遍：候选缺失但有推导规则的行，用同表已算行补算（确定性，不经 LLM）。
        for index, row_def in enumerate(definition.rows):
            row = rows[index]
            if row.value is not None or row_def.derivation is None:
                continue
            derived_value, ok = _derive_row_value(
                row_def.derivation, row_by_label, comparator=False
            )
            derived_cmp: Decimal | None = None
            if comparator_accession is not None and comparator_year is not None:
                derived_cmp, ok_cmp = _derive_row_value(
                    row_def.derivation, row_by_label, comparator=True
                )
                ok = ok and ok_cmp
            if not ok:
                all_limitations.append(
                    f"{kind.value}.{row_def.label}: NO_DERIVATION 推导输入缺失"
                )
                continue
            rows[index] = row.model_copy(
                update={
                    "value": derived_value,
                    "comparator_value": derived_cmp,
                    "derivation_source": row_def.derivation.inputs,
                }
            )
        sets.append(
            FinancialStatementSet(
                kind=kind,
                label=definition.label,
                target_year=target_year,
                comparator_year=comparator_year,
                rows=tuple(rows),
                limitations=tuple(dict.fromkeys(all_limitations)),
                balance_check=(
                    _balance_check(rows) if kind is FinancialStatementKind.BALANCE_SHEET else None
                ),
            )
        )
    return tuple(sets)


def _balance_check(rows: list[FinancialStatementRow]) -> BalanceCheckResult | None:
    """资产 == 负债 + 权益 勾稽校验（任一缺失则返回不可校验）。"""
    by_label: dict[str, Decimal] = {
        row.label: row.value for row in rows if row.value is not None
    }
    assets = by_label.get("资产总计")
    liabilities = by_label.get("负债合计")
    equity = by_label.get("股东权益")
    if assets is None or liabilities is None or equity is None:
        return BalanceCheckResult(ok=False, message="缺少资产/负债/权益任一项，无法校验勾稽关系")
    difference = assets - (liabilities + equity)
    if difference == 0:
        return BalanceCheckResult(ok=True, message="资产 = 负债 + 权益 成立")
    return BalanceCheckResult(ok=False, message=f"资产 - (负债 + 权益) = {difference}")


__all__ = [
    "BalanceCheckResult",
    "FinancialStatementKind",
    "FinancialStatementRow",
    "FinancialStatementSet",
    "StatementDef",
    "StatementMapping",
    "StatementPeriodType",
    "StatementRowDef",
    "STATEMENTS_V1_PATH",
    "extract_statements",
    "load_statement_mapping",
]
